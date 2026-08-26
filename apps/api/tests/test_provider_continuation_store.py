"""Security and scope tests for encrypted Provider continuation state."""

from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeObjectStorage
from nexuspilot_models.contracts import (
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
)
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from nexuspilot_api.core.config import Settings
from nexuspilot_api.infrastructure.provider_continuation_store import (
    ProviderContinuationStateError,
    ProviderContinuationStore,
)
from nexuspilot_api.models import (
    AttemptStatus,
    LlmModelAttempt,
    LlmRun,
    RunStatus,
    User,
)


async def _create_parent_attempt(db_session: AsyncSession) -> LlmModelAttempt:
    """Create the minimum valid ownership chain for one continuation-state record."""

    db_session.add(User(user_id="user-1", display_name="Continuation test user"))
    await db_session.flush()
    db_session.add(
        LlmRun(
            run_id="run-1",
            user_id="user-1",
            user_request="test continuation",
            status=RunStatus.RUNNING,
        )
    )
    await db_session.flush()
    model_attempt = LlmModelAttempt(
        attempt_id="attempt-1",
        run_id="run-1",
        provider=ProviderName.DEEPSEEK.value,
        model="deepseek-reasoner",
        status=AttemptStatus.COMPLETED,
    )
    db_session.add(model_attempt)
    await db_session.commit()
    return model_attempt


def _test_settings() -> Settings:
    """Return an isolated dedicated continuation key without production credentials."""

    return Settings(
        environment="test",
        api_key="test-api-key-long-enough",
        internal_api_key=SecretStr("test-internal-key-long-enough"),
        provider_continuation_encryption_key=SecretStr("c" * 32),
    )


async def test_continuation_state_is_encrypted_and_round_trips_with_exact_scope(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify plaintext is absent from storage and exact invocation scope can decrypt it."""

    storage = FakeObjectStorage()
    async with test_database_session_factory() as db_session:
        await _create_parent_attempt(db_session)
        continuation_store = ProviderContinuationStore(
            db_session=db_session,
            storage=storage,
            settings=_test_settings(),
        )
        state = ProviderContinuationState(
            kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
            raw_reasoning_for_tool_continuation="private chain of thought",
            tool_call_ids=["call-1"],
        )
        record = await continuation_store.prepare_persisted_state(
            parent_attempt_id="attempt-1",
            run_id="run-1",
            task_id=None,
            provider=ProviderName.DEEPSEEK,
            model="deepseek-reasoner",
            state=state,
        )
        db_session.add(record)
        await db_session.commit()

        encrypted_bytes = storage.objects[record.encrypted_payload_uri][0]
        assert b"private chain of thought" not in encrypted_bytes
        restored = await continuation_store.load_state(
            parent_attempt_id="attempt-1",
            run_id="run-1",
            task_id=None,
            provider=ProviderName.DEEPSEEK,
            model="deepseek-reasoner",
        )
        assert restored == state


async def test_continuation_state_rejects_cross_scope_and_tampered_ciphertext(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reject scope substitution before decryption and reject modified encrypted bytes."""

    storage = FakeObjectStorage()
    async with test_database_session_factory() as db_session:
        await _create_parent_attempt(db_session)
        continuation_store = ProviderContinuationStore(
            db_session=db_session,
            storage=storage,
            settings=_test_settings(),
        )
        record = await continuation_store.prepare_persisted_state(
            parent_attempt_id="attempt-1",
            run_id="run-1",
            task_id=None,
            provider=ProviderName.DEEPSEEK,
            model="deepseek-reasoner",
            state=ProviderContinuationState(
                kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
                raw_reasoning_for_tool_continuation="private chain of thought",
            ),
        )
        db_session.add(record)
        await db_session.commit()

        with pytest.raises(ProviderContinuationStateError, match="does not belong"):
            await continuation_store.load_state(
                parent_attempt_id="attempt-1",
                run_id="another-run",
                task_id=None,
                provider=ProviderName.DEEPSEEK,
                model="deepseek-reasoner",
            )
        encrypted_bytes, content_type = storage.objects[record.encrypted_payload_uri]
        storage.objects[record.encrypted_payload_uri] = (
            encrypted_bytes[:-1] + bytes([encrypted_bytes[-1] ^ 1]),
            content_type,
        )
        with pytest.raises(ProviderContinuationStateError, match="integrity"):
            await continuation_store.load_state(
                parent_attempt_id="attempt-1",
                run_id="run-1",
                task_id=None,
                provider=ProviderName.DEEPSEEK,
                model="deepseek-reasoner",
            )


async def test_expired_continuation_cleanup_removes_ciphertext_and_metadata(
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Physically remove an expired object and its lookup metadata in one bounded pass."""

    storage = FakeObjectStorage()
    async with test_database_session_factory() as db_session:
        await _create_parent_attempt(db_session)
        continuation_store = ProviderContinuationStore(
            db_session=db_session,
            storage=storage,
            settings=_test_settings(),
        )
        record = await continuation_store.prepare_persisted_state(
            parent_attempt_id="attempt-1",
            run_id="run-1",
            task_id=None,
            provider=ProviderName.DEEPSEEK,
            model="deepseek-reasoner",
            state=ProviderContinuationState(
                kind=ProviderContinuationKind.DEEPSEEK_RAW_REASONING,
                raw_reasoning_for_tool_continuation="expires soon",
            ),
        )
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.add(record)
        await db_session.commit()

        assert await continuation_store.purge_expired_states(limit=1) == 1
        assert record.encrypted_payload_uri not in storage.objects
        assert await db_session.get(type(record), record.continuation_state_id) is None
