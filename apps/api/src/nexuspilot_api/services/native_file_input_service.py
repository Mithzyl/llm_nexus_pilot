"""Validate legacy native manifests and catalog policies without creating new model input.

New Context dispatch uses parsed artifacts. Native codecs remain low-level
compatibility contracts; this module does not rehydrate historical Context for generation.
"""

import base64
from typing import Literal

from anyio import to_thread
from nexuspilot_models.contracts import (
    NATIVE_MEDIA_TYPES_BY_PROVIDER,
    ContractModel,
    NativeFileContent,
    NativeInputCapabilities,
    NativeMediaType,
)
from pydantic import Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, ObjectStorageError
from nexuspilot_api.models import LlmModelCatalogVersion, LlmRunArtifact

NATIVE_ARTIFACT_SOURCE = "native_artifact"


class NativeArtifactManifest(ContractModel):
    """Pin identity, verified metadata and native routing policy without embedding bytes or URIs."""

    schema_version: Literal["native_artifact.v1"] = "native_artifact.v1"
    artifact_id: str = Field(min_length=36, max_length=36)
    filename: str
    media_type: NativeMediaType
    size_bytes: int = Field(gt=0)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    input_token_reserve: int = Field(gt=0)
    processing_mode: Literal["provider_native"] = "provider_native"
    coverage: Literal["provider_interpreted_not_locally_verified"] = (
        "provider_interpreted_not_locally_verified"
    )


def catalog_native_input_policy(entry: LlmModelCatalogVersion) -> NativeInputCapabilities | None:
    """Read exact-model policy; missing remains unknown and malformed historical data fails."""

    if not isinstance(entry.capabilities_json, dict):
        raise ResourceConflictError("Model Catalog capability metadata is invalid")
    if "native_input" not in entry.capabilities_json:
        return None
    try:
        policy = NativeInputCapabilities.model_validate(entry.capabilities_json["native_input"])
    except (ValidationError, TypeError) as exc:
        raise ResourceConflictError("Model Catalog native input policy is invalid") from exc
    if policy.media_types and (
        not entry.supports_vision
        or not set(policy.media_types).issubset(
            NATIVE_MEDIA_TYPES_BY_PROVIDER.get(entry.provider, frozenset())
        )
    ):
        raise ResourceConflictError(
            "Model Catalog native input policy conflicts with provider capabilities"
        )
    if policy.input_token_reserve_per_file >= entry.context_window:
        raise ResourceConflictError("Native input reservation exceeds the model context window")
    return policy


async def read_native_artifact(
    db_session: AsyncSession,
    storage: ObjectStorage,
    *,
    run_id: str,
    artifact_id: str,
    policy: NativeInputCapabilities,
    pinned_manifest: NativeArtifactManifest | None = None,
) -> tuple[NativeArtifactManifest, NativeFileContent]:
    """Read bounded bytes for a Run already authorized by Context Builder.

    No writes occur. Missing, changed or corrupt content raises a safe application
    error; callers must not use this internal helper as an unscoped download API.
    """

    artifact = await db_session.get(LlmRunArtifact, artifact_id)
    if artifact is None:
        raise ResourceNotFoundError("Artifact")
    if artifact.run_id != run_id:
        raise ResourceConflictError("Native Artifact does not belong to the Run")
    if (
        artifact.mime_type not in policy.media_types
        or not 0 < artifact.size_bytes <= policy.max_file_bytes
    ):
        raise ResourceConflictError("Native Artifact type or size is unsupported by this model")
    manifest = NativeArtifactManifest(
        artifact_id=artifact_id,
        filename=artifact.filename,
        media_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        content_hash=artifact.content_hash,
        input_token_reserve=policy.input_token_reserve_per_file,
    )
    if pinned_manifest is not None and manifest != pinned_manifest:
        raise ResourceConflictError("Native Artifact metadata changed after Context snapshot")
    try:
        opened = await storage.open_object(
            artifact.storage_uri, expected_size_bytes=manifest.size_bytes
        )
        if opened.size_bytes != manifest.size_bytes or opened.content_type != manifest.media_type:
            raise ResourceConflictError("Native Artifact storage metadata does not match")

        def collect_verified_size() -> bytes:
            """Bound lazy storage reads by verified size and close generators even on overflow."""

            collected = bytearray()
            chunks = iter(opened.chunks)
            try:
                for chunk in chunks:
                    if len(collected) + len(chunk) > manifest.size_bytes:
                        raise ResourceConflictError("Native Artifact stream exceeds declared size")
                    collected.extend(chunk)
            finally:
                close = getattr(chunks, "close", None)
                if close is not None:
                    close()
            if len(collected) != manifest.size_bytes:
                raise ResourceConflictError("Native Artifact stream is incomplete")
            return bytes(collected)

        content = await to_thread.run_sync(collect_verified_size)
    except ObjectStorageError as exc:
        raise ResourceConflictError("Native Artifact object is unavailable or invalid") from exc
    try:
        attachment = NativeFileContent(
            filename=manifest.filename,
            media_type=manifest.media_type,
            content_hash=manifest.content_hash,
            data_base64=base64.b64encode(content).decode("ascii"),
        )
    except ValidationError as exc:
        raise ResourceConflictError(
            "Native Artifact bytes failed integrity or format validation"
        ) from exc
    return manifest, attachment
