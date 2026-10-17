"""Async unit-of-work adapter for explicitly registered MinIO-backed conversation fields.

This is scoped to the application session factory, not a global ORM event hook.
SQL stores neutral legacy placeholders plus one immutable object reference. Reads
check ownership before opening objects and hydrate ORM values without marking them
dirty. API schemas therefore retain their historical response shape. Raw SQL is
reserved for migrations/metadata checks; it does not hydrate payloads.
"""

import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import JSON, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.infrastructure.conversation_body_fields import BODY_FIELDS
from nexuspilot_api.infrastructure.object_storage import ObjectStorage, ObjectStorageError
from nexuspilot_api.models.base import new_id

MAX_BODY_BYTES = 16 * 1024 * 1024


class ConversationBodySession(AsyncSession):
    """Persist registered payloads before SQL flush and hydrate authorized entity reads."""

    def __init__(self, *args, **kwargs):
        """Disable implicit sync autoflush so every write passes the async object writer."""
        kwargs["autoflush"] = False
        super().__init__(*args, **kwargs)
        self._body_metadata_depth = 0
        self._body_writing = False

    @property
    def body_storage(self):
        """Resolve the injected storage client, defaulting to the local API configuration."""
        if "body_storage" not in self.info:
            self.info["body_storage"] = ObjectStorage()
        return self.info["body_storage"]

    @contextmanager
    def body_metadata_only(self):
        """Permit authorization and maintenance to inspect SQL identity without reading bodies."""
        self._body_metadata_depth += 1
        try:
            yield
        finally:
            self._body_metadata_depth -= 1

    async def _read_body(self, record):
        """Check owner, object key, size, digest and record binding before restoring fields."""
        table = getattr(record, "__tablename__", None)
        if self._body_metadata_depth or table not in BODY_FIELDS:
            return
        reference = record.body_ref_json
        if reference is None:
            return  # Legacy SQL bodies remain readable; reads never migrate them.
        await self._authorize_body(record)
        key = str(getattr(record, inspect(type(record)).primary_key[0].key))
        try:
            digest, size = reference["sha256"], reference["size_bytes"]
            expected_name = f"conversation-bodies/{table}/{key}/{digest}.json"
            if (
                reference["version"] != 1
                or type(size) is not int
                or not 0 < size <= MAX_BODY_BYTES
                or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)
                or reference["uri"] != self.body_storage.uri_for_object(expected_name)
            ):
                raise ValueError("Invalid body reference")
            from nexuspilot_api.services.user_file_service import read_verified_bytes

            raw = await read_verified_bytes(self.body_storage, reference["uri"], size, digest)
            body = json.loads(raw)
            if body["version"] != 1 or body["table"] != table or body["record_id"] != key:
                raise ValueError("Body belongs to a different record")
            values = body["fields"]
            if not isinstance(values, dict) or set(values) != set(BODY_FIELDS[table]):
                raise ValueError("Body fields do not match the registered schema")
            for field, value in values.items():
                column = inspect(type(record)).columns[field]
                if (
                    not isinstance(column.type, JSON)
                    and value is not None
                    and not isinstance(value, str)
                ):
                    raise ValueError("Invalid text field")
                set_committed_value(record, field, value)
        except (KeyError, TypeError, ValueError, ObjectStorageError) as exc:
            raise ResourceConflictError("对话正文暂不可读取或校验失败，请稍后重试。") from exc

    async def _authorize_body(self, record):
        """Resolve persisted ownership using metadata queries before any MinIO read."""
        owner = self.info.get("authorized_user_id")
        if owner is not None:
            from nexuspilot_api.features.identity.resource_access import require_resource_owner

            with self.body_metadata_only():
                await require_resource_owner(
                    self,
                    type(record),
                    getattr(record, inspect(type(record)).primary_key[0].key),
                    owner,
                )

    async def _prepare_body(self, record):
        """Upload a verified immutable envelope and durable receipt before replacing SQL fields."""
        table = getattr(record, "__tablename__", None)
        if table not in BODY_FIELDS:
            return None
        mapper = inspect(type(record))
        if record.body_ref_json:
            changed = {
                field: getattr(record, field)
                for field in BODY_FIELDS[table]
                if inspect(record).attrs[field].history.has_changes()
            }
            await self._read_body(record)
            for field, value in changed.items():
                setattr(record, field, value)
        key_name = mapper.primary_key[0].key
        if getattr(record, key_name) is None:
            setattr(record, key_name, new_id())
        key = str(getattr(record, key_name))
        values = {}
        for field in BODY_FIELDS[table]:
            if field not in record.__dict__:
                if inspect(record).persistent:
                    await super().refresh(record, attribute_names=[field])
                else:
                    default = mapper.columns[field].default
                    if default is not None:
                        setattr(
                            record, field, default.arg(None) if default.is_callable else default.arg
                        )
            values[field] = copy.deepcopy(getattr(record, field, None))
        raw = json.dumps(
            {"version": 1, "table": table, "record_id": key, "fields": values},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(raw) > MAX_BODY_BYTES:
            raise ResourceConflictError("对话正文超过单条对象大小限制。")
        digest = hashlib.sha256(raw).hexdigest()
        reference = record.body_ref_json
        if not reference or reference.get("sha256") != digest:
            name = f"conversation-bodies/{table}/{key}/{digest}.json"
            reference = {
                "version": 1,
                "uri": self.body_storage.uri_for_object(name),
                "sha256": digest,
                "size_bytes": len(raw),
            }
            # Receipts survive SQL rollback and uncertain commit outcomes. Maintenance
            # verifies SQL references before deleting exact keys, never on commit error.
            receipt = {
                "version": 1,
                "table": table,
                "record_id": key,
                "reference": reference,
                "created_at": datetime.now(UTC).isoformat(),
            }
            await self.body_storage.put_bytes(
                f"conversation-body-writes/{new_id()}.json",
                json.dumps(receipt).encode(),
                "application/json",
            )
            stored = await self.body_storage.put_bytes(name, raw, "application/json")
            from nexuspilot_api.services.user_file_service import read_verified_bytes

            await read_verified_bytes(self.body_storage, stored.uri, len(raw), digest)
            record.body_ref_json = reference
        for field in values:
            column = mapper.columns[field]
            setattr(
                record,
                field,
                None if column.nullable else ({} if isinstance(column.type, JSON) else ""),
            )
        return record, values

    async def flush(self, objects=None):
        """Write objects before SQL; restore only in-memory values after a successful flush."""
        if self._body_writing:
            return await super().flush(objects)
        self._body_writing = True
        saved = []
        try:
            for record in list(self.new | self.dirty):
                body = await self._prepare_body(record)
                if body is not None:
                    saved.append(body)
            await super().flush(objects)
            for record, values in saved:
                for field, value in values.items():
                    set_committed_value(record, field, value)
        except BaseException:
            # Restore the SQL transaction, not objects whose commit outcome may be
            # unknown. Their receipts remain available for offline reconciliation.
            await super().rollback()
            raise
        finally:
            self._body_writing = False

    async def commit(self):
        """Flush external bodies before committing SQL; retain receipts on uncertain outcomes."""
        await self.flush()
        await super().commit()

    async def execute(self, statement, params=None, **kwargs):
        """Materialize bounded ORM results and restore bodies after SQL identity checks."""
        if not self._body_metadata_depth and not self._body_writing:
            await self.flush()
        if getattr(statement, "is_update", False):
            fields = BODY_FIELDS.get(statement.table.name, ())
            assigned = {
                getattr(key, "key", key) for key in (getattr(statement, "_values", None) or {})
            }
            if assigned.intersection(fields):
                raise ResourceConflictError("Update conversation body fields through ORM records")
        result = await super().execute(statement, params, **kwargs)
        if self._body_metadata_depth or not getattr(statement, "is_select", False):
            return result
        frozen = result.freeze()
        for row in frozen():
            for record in row:
                await self._read_body(record)
        return frozen()

    async def scalar(self, statement, params=None, **kwargs):
        """Route scalar ORM reads through the same verified payload path."""
        return (await self.execute(statement, params, **kwargs)).scalar()

    async def scalars(self, statement, params=None, **kwargs):
        """Route scalar collections through the same verified payload path."""
        return (await self.execute(statement, params, **kwargs)).scalars()

    async def get(self, entity, ident, **kwargs):
        """Load one ORM identity and verify any referenced conversation body."""
        if not self._body_metadata_depth:
            await self.flush()
        record = await super().get(entity, ident, **kwargs)
        if record is not None:
            await self._read_body(record)
        return record

    async def refresh(self, instance, attribute_names=None, **kwargs):
        """Restore body values after explicit refresh without writing them back into SQL."""
        await super().refresh(instance, attribute_names=attribute_names, **kwargs)
        await self._read_body(instance)
