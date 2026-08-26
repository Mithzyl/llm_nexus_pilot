"""Encrypt, persist, and scope provider-only reasoning continuation payloads."""

import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from nexuspilot_models.contracts import ProviderContinuationState, ProviderName
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.config import Settings
from nexuspilot_api.infrastructure.object_storage import (
    ObjectStorage,
    ObjectStorageError,
    ObjectStorageNotFoundError,
)
from nexuspilot_api.models import LlmProviderContinuationState, new_id

_PAYLOAD_PREFIX = b"NPCS1"
_KEY_VERSION = "v1"
_CLEANUP_BATCH_SIZE = 32
logger = logging.getLogger(__name__)


class ProviderContinuationStateError(RuntimeError):
    """Report missing, expired, corrupt, or cross-scope continuation state safely."""


class ProviderContinuationStore:
    """Keep sensitive Provider state encrypted and bound to its parent model invocation."""

    def __init__(
        self,
        *,
        db_session: AsyncSession,
        storage: ObjectStorage,
        settings: Settings,
    ) -> None:
        """Bind one request-scoped database session, object store, and encryption policy."""

        self.db_session = db_session
        self.storage = storage
        self.settings = settings
        self._encryption_key = hashlib.sha256(
            settings.continuation_encryption_secret().encode()
        ).digest()

    async def prepare_persisted_state(
        self,
        *,
        parent_attempt_id: str,
        run_id: str,
        task_id: str | None,
        provider: ProviderName,
        model: str,
        state: ProviderContinuationState,
    ) -> LlmProviderContinuationState:
        """Encrypt one continuation payload and return metadata for the caller's transaction."""

        await self.purge_expired_states(limit=_CLEANUP_BATCH_SIZE)
        associated_data = self._associated_data(
            parent_attempt_id=parent_attempt_id,
            run_id=run_id,
            task_id=task_id,
            provider=provider.value,
            model=model,
        )
        plaintext = state.model_dump_json().encode()
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._encryption_key).encrypt(
            nonce,
            plaintext,
            associated_data,
        )
        encrypted_payload = _PAYLOAD_PREFIX + nonce + ciphertext
        stored_payload = await self.storage.put_bytes(
            f"{run_id}/{parent_attempt_id}/provider-continuation.bin",
            encrypted_payload,
            "application/octet-stream",
        )
        return LlmProviderContinuationState(
            continuation_state_id=new_id(),
            parent_attempt_id=parent_attempt_id,
            run_id=run_id,
            task_id=task_id,
            provider=provider.value,
            model=model,
            state_kind=state.kind.value,
            encrypted_payload_uri=stored_payload.uri,
            encryption_key_version=_KEY_VERSION,
            payload_hash=stored_payload.content_hash,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=self.settings.provider_continuation_retention_seconds),
        )

    async def purge_expired_states(self, *, limit: int) -> int:
        """Delete a bounded batch of expired ciphertext while retaining failed deletions."""

        expired_records = list(
            await self.db_session.scalars(
                select(LlmProviderContinuationState)
                .where(LlmProviderContinuationState.expires_at <= datetime.now(UTC))
                .order_by(LlmProviderContinuationState.expires_at)
                .limit(limit)
            )
        )
        deleted_count = 0
        for record in expired_records:
            try:
                await self.storage.delete_object(record.encrypted_payload_uri)
            except ObjectStorageNotFoundError:
                pass
            except ObjectStorageError:
                logger.warning(
                    "Expired Provider continuation cleanup deferred after storage failure."
                )
                continue
            await self.db_session.delete(record)
            deleted_count += 1
        if deleted_count:
            await self.db_session.commit()
        return deleted_count

    async def load_state(
        self,
        *,
        parent_attempt_id: str,
        run_id: str,
        task_id: str | None,
        provider: ProviderName,
        model: str,
    ) -> ProviderContinuationState:
        """Load state only when every invocation-scope field matches and it remains valid."""

        record = await self.db_session.scalar(
            select(LlmProviderContinuationState).where(
                LlmProviderContinuationState.parent_attempt_id == parent_attempt_id
            )
        )
        if record is None:
            raise ProviderContinuationStateError("Provider continuation state was not found.")
        if (
            record.run_id != run_id
            or record.task_id != task_id
            or record.provider != provider.value
            or record.model != model
        ):
            raise ProviderContinuationStateError(
                "Provider continuation state does not belong to this invocation scope."
            )
        expires_at = record.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if record.superseded_at is not None or expires_at <= datetime.now(UTC):
            raise ProviderContinuationStateError("Provider continuation state has expired.")

        object_content = await self.storage.open_object(record.encrypted_payload_uri)
        encrypted_payload = b"".join(object_content.chunks)
        if hashlib.sha256(encrypted_payload).hexdigest() != record.payload_hash:
            raise ProviderContinuationStateError(
                "Provider continuation state failed integrity checks."
            )
        if not encrypted_payload.startswith(_PAYLOAD_PREFIX) or len(encrypted_payload) <= 17:
            raise ProviderContinuationStateError("Provider continuation state is corrupt.")
        nonce = encrypted_payload[len(_PAYLOAD_PREFIX) : len(_PAYLOAD_PREFIX) + 12]
        ciphertext = encrypted_payload[len(_PAYLOAD_PREFIX) + 12 :]
        associated_data = self._associated_data(
            parent_attempt_id=parent_attempt_id,
            run_id=run_id,
            task_id=task_id,
            provider=provider.value,
            model=model,
        )
        try:
            plaintext = AESGCM(self._encryption_key).decrypt(
                nonce,
                ciphertext,
                associated_data,
            )
            payload = json.loads(plaintext)
            return ProviderContinuationState.model_validate(payload)
        except Exception as exc:
            raise ProviderContinuationStateError(
                "Provider continuation state could not be decrypted."
            ) from exc

    @staticmethod
    def _associated_data(
        *,
        parent_attempt_id: str,
        run_id: str,
        task_id: str | None,
        provider: str,
        model: str,
    ) -> bytes:
        """Serialize stable scope fields as authenticated encryption metadata."""

        return json.dumps(
            {
                "parent_attempt_id": parent_attempt_id,
                "run_id": run_id,
                "task_id": task_id,
                "provider": provider,
                "model": model,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
