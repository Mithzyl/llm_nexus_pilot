"""Shared MinIO snapshot protocol and deterministic Memory JSON/Markdown helpers.

L1-L4 Memory snapshots use the documented five-step pointer protocol: create a
``generating`` business version, upload and verify JSON/Markdown objects, register
the objects, then atomically activate and switch the MySQL current pointer. This
module owns object-key construction and the upload/verify/register steps so that
every layer follows the same failure semantics.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import LlmMemorySnapshotObject, SnapshotObjectStatus, new_id
from nexuspilot_api.models.base import utc_now


@dataclass(frozen=True)
class RegisteredSnapshot:
    """Identify the two immutable objects that back one business snapshot version."""

    json_object_id: str
    markdown_object_id: str
    json_storage_uri: str
    markdown_storage_uri: str
    content_hash: str


def build_snapshot_object_key(
    *,
    memory_layer: str,
    scope_id: str,
    object_type: str,
    version: int,
    object_id: str,
    extension: str,
    created_at: datetime | None = None,
) -> str:
    """Build a privacy-safe deterministic object key with no personal plaintext."""

    timestamp = created_at or utc_now()
    object_hash = hashlib.sha256(object_id.encode()).hexdigest()[:8]
    return (
        f"memory/v1/{memory_layer}/{scope_id}/{object_type}/"
        f"{timestamp.year:04d}/{timestamp.month:02d}/{timestamp.day:02d}/"
        f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}_"
        f"v{version:06d}_{object_id}_{object_hash}.{extension}"
    )


def render_envelope(
    *,
    schema_version: str,
    object_id: str,
    version: int,
    source_ids: list[str],
    content: dict[str, Any],
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Wrap structured Memory content in the versioned immutable envelope contract."""

    return {
        "schema_version": schema_version,
        "object_id": object_id,
        "version": version,
        "created_at": (created_at or utc_now()).isoformat(),
        "source_ids": source_ids,
        "content": content,
    }


async def upload_and_register_snapshot(
    *,
    db_session: AsyncSession,
    storage: ObjectStorage,
    user_id: str,
    memory_layer: str,
    scope_id: str,
    object_type: str,
    schema_version: str,
    version: int,
    object_id: str,
    content: dict[str, Any],
    source_ids: list[str],
    project_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    task_id: str | None = None,
    created_at: datetime | None = None,
) -> RegisteredSnapshot:
    """Upload and verify JSON/Markdown snapshots, then register both object rows."""

    timestamp = created_at or utc_now()
    envelope = render_envelope(
        schema_version=schema_version,
        object_id=object_id,
        version=version,
        source_ids=source_ids,
        content=content,
        created_at=timestamp,
    )
    json_bytes = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode()
    markdown_bytes = render_markdown_view(
        schema_version=schema_version,
        object_id=object_id,
        version=version,
        source_ids=source_ids,
        content=content,
    ).encode()
    json_key = build_snapshot_object_key(
        memory_layer=memory_layer,
        scope_id=scope_id,
        object_type=f"{object_type}.json",
        version=version,
        object_id=object_id,
        extension="json",
        created_at=timestamp,
    )
    markdown_key = build_snapshot_object_key(
        memory_layer=memory_layer,
        scope_id=scope_id,
        object_type=f"{object_type}.md",
        version=version,
        object_id=object_id,
        extension="md",
        created_at=timestamp,
    )
    json_stored = await storage.put_bytes(json_key, json_bytes, "application/json")
    markdown_stored = await storage.put_bytes(markdown_key, markdown_bytes, "text/markdown")
    # Step 4: re-check object metadata and hashes before any database registration.
    json_verified = await storage.verify_object(json_stored.uri, json_stored.content_hash)
    markdown_verified = await storage.verify_object(
        markdown_stored.uri, markdown_stored.content_hash
    )
    if not json_verified or not markdown_verified:
        raise ResourceConflictError("Snapshot object hash verification failed")
    if json_stored.content_hash != hashlib.sha256(json_bytes).hexdigest():
        raise ResourceConflictError("Snapshot object hash mismatch")
    json_object_id = new_id()
    markdown_object_id = new_id()
    db_session.add_all(
        [
            LlmMemorySnapshotObject(
                memory_snapshot_object_id=json_object_id,
                memory_layer=memory_layer,
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
                run_id=run_id,
                task_id=task_id,
                object_type=f"{object_type}.json",
                schema_version=schema_version,
                object_version=version,
                storage_uri=json_stored.uri,
                content_hash=json_stored.content_hash,
                size_bytes=json_stored.size_bytes,
                mime_type="application/json",
                status=SnapshotObjectStatus.ACTIVE,
            ),
            LlmMemorySnapshotObject(
                memory_snapshot_object_id=markdown_object_id,
                memory_layer=memory_layer,
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
                run_id=run_id,
                task_id=task_id,
                object_type=f"{object_type}.md",
                schema_version=schema_version,
                object_version=version,
                storage_uri=markdown_stored.uri,
                content_hash=markdown_stored.content_hash,
                size_bytes=markdown_stored.size_bytes,
                mime_type="text/markdown",
                status=SnapshotObjectStatus.ACTIVE,
            ),
        ]
    )
    return RegisteredSnapshot(
        json_object_id=json_object_id,
        markdown_object_id=markdown_object_id,
        json_storage_uri=json_stored.uri,
        markdown_storage_uri=markdown_stored.uri,
        content_hash=json_stored.content_hash,
    )


def render_markdown_view(
    *,
    schema_version: str,
    object_id: str,
    version: int,
    source_ids: list[str],
    content: dict[str, Any],
) -> str:
    """Render a deterministic human/model-readable Markdown view of one snapshot."""

    lines = [
        f"# Memory Snapshot ({schema_version})",
        "",
        f"- object_id: `{object_id}`",
        f"- version: {version}",
        f"- source_ids: {', '.join(source_ids) or 'none'}",
        "",
    ]
    _render_markdown_section(lines, content, indent=0)
    return "\n".join(lines).rstrip() + "\n"


def _render_markdown_section(lines: list[str], value: Any, *, indent: int) -> None:
    """Recursively render one envelope content section with stable ordering."""

    prefix = "  " * indent
    if isinstance(value, dict):
        for key in sorted(value):
            lines.append(f"{prefix}- **{key}**:")
            _render_markdown_section(lines, value[key], indent=indent + 1)
    elif isinstance(value, list):
        if not value:
            lines.append(f"{prefix}  (empty)")
            return
        for index, item in enumerate(value, start=1):
            lines.append(f"{prefix}- item {index}:")
            _render_markdown_section(lines, item, indent=indent + 1)
    else:
        lines.append(f"{prefix}  {value}")
