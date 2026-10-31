"""Scoped personal/project memory with encrypted objects and explicit mutation results.

Legacy Memory rows are deliberately not imported or activated. This service owns
its transaction; tool callers must use a separate session to isolate rollbacks.
"""

import asyncio
import base64
import json
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import TypeAdapter
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.memory.models.product_memory import (
    UserMemoryDisclosure,
    UserMemoryEntry,
    UserMemoryOperation,
    UserMemoryScope,
    UserMemoryVersion,
)
from nexuspilot_api.features.memory.schemas.product_memory import AppMemoryData
from nexuspilot_api.features.memory.schemas.typed_memory import TypedMemory
from nexuspilot_api.features.memory.services.credential_guard import scan_for_sensitive_input
from nexuspilot_api.features.memory.services.disclosures import disclosure_condition, is_disclosed
from nexuspilot_api.models import LlmProject, User
from nexuspilot_api.models.base import new_id, utc_now


class MemoryFailure(Exception):
    """Expose a bounded error code without private content, storage paths or key material."""


def sensitive_text(text):
    """Conservatively require confirmation for address, coordinate and private-event markers."""
    return bool(
        re.search(
            r"家庭|住址|地址|身份证|银行卡|密码|病|健康|财务|工资|行程|日程|坐标|经度|纬度|"
            r"home address|health|salary|latitude|longitude|\d{1,3}\.\d{4,}",
            text,
            re.I,
        )
    )


class MemoryService:
    """Keep every operation inside one authenticated owner's entry and optional Nexus project."""

    def __init__(
        self,
        db,
        storage,
        owner,
        entry,
        project_id=None,
        run_id=None,
        require_running=False,
        assistant_automatic=False,
    ):
        """Accept identity only from trusted callers, never model arguments."""
        self.db, self.storage, self.owner = db, storage, owner
        self.entry, self.project_id, self.run_id = entry, project_id, run_id
        self.require_running = require_running
        self.native_management_grant = None
        self.assistant_automatic = bool(
            assistant_automatic and entry == "personal_assistant" and not project_id
        )
        self.scope_key = f"{entry}:{project_id or 'personal'}"

    async def authorize(self):
        """Recheck active ownership and deny unimplemented App project mapping."""
        user = await self.db.get(User, self.owner, populate_existing=True)
        if (
            user is None
            or not user.is_active
            or self.entry
            not in {
                "native",
                "personal_assistant",
                "retail",
            }
        ):
            raise MemoryFailure("memory_not_allowed")
        if self.project_id:
            project = await self.db.get(LlmProject, self.project_id, populate_existing=True)
            if (
                self.entry != "native"
                or project is None
                or project.owner_user_id != self.owner
                or project.status.value != "active"
            ):
                raise MemoryFailure("memory_project_not_allowed")

    async def scope(self, project_id, *, create=False):
        """Load or serialize the scope row; default-enabled reads do not write database rows."""
        key = f"{self.entry}:{project_id or 'personal'}"
        query = (
            select(UserMemoryScope)
            .where(
                UserMemoryScope.owner_user_id == self.owner,
                UserMemoryScope.scope_key == key,
            )
            .execution_options(populate_existing=True)
        )
        row = await self.db.scalar(query.with_for_update() if create else query)
        if row is None and create:
            try:
                async with self.db.begin_nested():
                    row = UserMemoryScope(
                        owner_user_id=self.owner,
                        entry=self.entry,
                        scope_key=key,
                        project_id=project_id,
                        enabled=True,
                        version=0,
                    )
                    self.db.add(row)
                    await self.db.flush()
            except IntegrityError:
                row = await self.db.scalar(query.with_for_update())
                if row is None:
                    raise MemoryFailure("memory_scope_conflict") from None
        return row

    async def settings(self):
        """Resolve entry-wide enablement and a project's inherit/disabled setting."""
        await self.authorize()
        parent = await self.scope(None)
        own = await self.scope(self.project_id) if self.project_id else parent
        return {
            "enabled": (parent is None or parent.enabled) and (own is None or own.enabled),
            "mode": ("inherit" if own is None or own.enabled else "disabled")
            if self.project_id
            else "entry",
            "version": own.version if own else 0,
            "entry_enabled": parent is None or parent.enabled,
            "automatic_native_use": bool(
                own
                and own.native_use_grant
                and self.native_management_grant
                and own.native_use_grant == self.native_management_grant
            ),
        }

    def cipher(self):
        """Require an independent 32-byte URL-safe Base64 key; never fall back to plaintext."""
        setting = get_settings().memory_encryption_key
        try:
            key = base64.b64decode(setting.get_secret_value(), altchars=b"-_", validate=True)
            if len(key) != 32:
                raise ValueError()
            return AESGCM(key)
        except (AttributeError, ValueError):
            raise MemoryFailure("memory_key_unavailable") from None

    async def put(self, value):
        """Encrypt content with scope-bound authenticated data before using existing storage."""
        nonce = os.urandom(12)
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        aad = f"{self.owner}:{self.scope_key}".encode()
        encrypted = nonce + self.cipher().encrypt(nonce, data, aad)
        try:
            stored = await self.storage.put_bytes(
                f"private-memory/{self.owner}/{new_id()}",
                encrypted,
                "application/octet-stream",
            )
            return stored.uri
        except Exception:
            raise MemoryFailure("memory_storage_unavailable") from None

    async def read(self, uri):
        """Decrypt a private object only after caller scope checks; never return raw failures."""
        cipher = self.cipher()
        try:
            obj = await self.storage.open_object(uri)
            if obj.size_bytes > 65536:
                raise ValueError()
            data = await asyncio.to_thread(b"".join, obj.chunks)
            return json.loads(
                cipher.decrypt(data[:12], data[12:], f"{self.owner}:{self.scope_key}".encode())
            )
        except Exception:
            raise MemoryFailure("memory_content_unavailable") from None

    async def query(
        self,
        *,
        automatic=False,
        for_model=False,
        offset=0,
        limit=20,
        query="",
        include_candidates=False,
    ):
        """Recall own Assistant facts; other entries retain per-Run sensitive disclosure."""
        settings = await self.settings()
        if automatic and not settings["enabled"]:
            return {"items": [], "next_offset": None, "disabled": True}
        own = await self.scope(self.project_id)
        if own is None:
            return {"items": [], "next_offset": None}
        # Explicit current-Run grants are authoritative; caller/model IDs are not.
        # Corrections change the content version, invalidating all previous Run grants.
        if (
            for_model
            and self.run_id
            and self.project_id is None
            and self.entry != "personal_assistant"
        ):
            granted = list(
                await self.db.scalars(
                    select(UserMemoryEntry)
                    .where(
                        UserMemoryEntry.scope_id == own.scope_id,
                        UserMemoryEntry.status == "active",
                        disclosure_condition(self.run_id, self.entry),
                    )
                    .order_by(UserMemoryEntry.entry_id)
                )
            )
            if granted:
                authorized = []
                for row in granted:
                    body = await self.read(row.body_uri)
                    authorized.append(
                        {
                            "entry_id": row.entry_id,
                            "version": row.version,
                            "memory_type": row.memory_type,
                            "schema_version": row.schema_version,
                            "status": row.status,
                            "sensitive": row.sensitive,
                            **body,
                        }
                    )
                terms = [term.casefold() for term in re.split(r"[\s,，、;；]+", query) if term]
                matches = [
                    item
                    for item in authorized
                    if not terms or any(term in item["text"].casefold() for term in terms)
                ]
                selected = matches or authorized
                return {
                    "items": selected[offset : offset + limit],
                    "next_offset": offset + limit if len(selected) > offset + limit else None,
                    "authorized_fallback": bool(terms and not matches),
                }
        rows = list(
            await self.db.scalars(
                select(UserMemoryEntry)
                .where(
                    UserMemoryEntry.scope_id == own.scope_id,
                    UserMemoryEntry.status != "forgotten",
                )
                .order_by(UserMemoryEntry.entry_id)
                .offset(offset)
                .limit(min(limit, 50) + 1)
            )
        )
        items = []
        for row in rows[:limit]:
            if for_model and row.status != "active" and not include_candidates:
                continue
            item = {
                "entry_id": row.entry_id,
                "version": row.version,
                "status": row.status,
                "sensitive": row.sensitive,
            }
            if (
                for_model
                and row.sensitive
                and self.entry != "personal_assistant"
                and not await is_disclosed(self.db, row, self.run_id, self.entry)
            ):
                # Do not leak content or keyword matches before disclosure, but
                # distinguish an undisclosed entry from an empty account.
                item["disclosure_required"] = True
            else:
                body = await self.read(row.body_uri)
                if query and query.casefold() not in body["text"].casefold():
                    continue
                item["text"] = body["text"]
                if body.get("app_data") is not None:
                    item["app_data"] = body["app_data"]
                if body.get("typed_data") is not None:
                    item["typed_data"] = body["typed_data"]
            item.update(memory_type=row.memory_type, schema_version=row.schema_version)
            items.append(item)
        return {"items": items, "next_offset": offset + limit if len(rows) > limit else None}

    async def mutate(self, request):
        """Commit one scoped operation or roll it back; object failures never publish success."""
        try:
            return await self._mutate(request)
        except BaseException:
            await self.db.rollback()
            raise

    async def history(self, entry_id, *, offset=0, limit=20):
        """Read owned encrypted version history, including forgotten entries."""
        await self.authorize()
        scope = await self.scope(self.project_id)
        row = await self.db.get(UserMemoryEntry, entry_id)
        if scope is None or row is None or row.scope_id != scope.scope_id:
            raise MemoryFailure("memory_not_found")
        versions = list(
            await self.db.scalars(
                select(UserMemoryVersion)
                .where(UserMemoryVersion.entry_id == entry_id, UserMemoryVersion.version > 0)
                .order_by(UserMemoryVersion.version.desc())
                .offset(offset)
                .limit(limit + 1)
            )
        )
        items = []
        for version in versions[:limit]:
            body = await self.read(version.body_uri) if version.body_uri else {}
            items.append({"version": version.version, "status": version.status, **body})
        return {
            "entry_id": entry_id,
            "current_version": row.version,
            "items": items,
            "next_offset": offset + limit if len(versions) > limit else None,
        }

    async def retain_version(self, row, *, initial=False):
        """Capture a locked version; version zero represents a new entry's absence."""
        version = 0 if initial else row.version
        if await self.db.get(UserMemoryVersion, (row.entry_id, version)) is None:
            self.db.add(
                UserMemoryVersion(
                    entry_id=row.entry_id,
                    version=version,
                    status="forgotten" if initial else row.status,
                    sensitive=row.sensitive,
                    body_uri=None if initial else row.body_uri,
                    memory_type=row.memory_type,
                    schema_version=row.schema_version,
                )
            )
            await self.db.flush()

    async def _mutate(self, request):
        """Lock scope settings and update versions, retaining encrypted retry comparison."""
        await self.authorize()
        parent = await self.scope(None, create=True)
        own = await self.scope(self.project_id, create=True) if self.project_id else parent
        parent_version = parent.version
        previous = await self.db.scalar(
            select(UserMemoryOperation).where(
                UserMemoryOperation.scope_id == own.scope_id,
                UserMemoryOperation.request_key == request["key"],
            )
        )
        if previous:
            if await self.read(previous.request_uri) != request:
                raise MemoryFailure("memory_request_conflict")
            result = dict(previous.result_json)
            if result.get("entry_id"):
                result.setdefault("applied_version", result["version"])
                latest = await self.db.scalar(
                    select(UserMemoryEntry)
                    .where(UserMemoryEntry.entry_id == result["entry_id"])
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                result.update(status=latest.status, version=latest.version)
            elif request["action"] == "settings":
                result = await self.settings()
            await self.db.commit()
            return result
        action = request["action"]
        if self.assistant_automatic and not (parent.enabled and own.enabled):
            raise MemoryFailure("memory_disabled")
        typed_data = request.get("typed_data")
        if typed_data is not None:
            if action not in {"save", "propose", "correct"}:
                raise MemoryFailure("memory_invalid_operation")
            if not request.get("confirmed_type") and not self.assistant_automatic:
                raise MemoryFailure("memory_type_confirmation_required")
            typed_data = (
                TypeAdapter(TypedMemory).validate_python(typed_data).model_dump(mode="json")
            )
        app_data = request.get("app_data")
        if app_data is not None:
            app_data = AppMemoryData.model_validate(app_data).model_dump(mode="json")
            if app_data["namespace"] != self.entry or self.project_id:
                raise MemoryFailure("memory_app_scope_mismatch")
        if action in {"save", "propose", "confirm"} and not (parent.enabled and own.enabled):
            raise MemoryFailure("memory_disabled")
        text = request.get("text", "")
        scanned = text + json.dumps([app_data, typed_data], ensure_ascii=False)
        if scanned and (
            scan_for_sensitive_input(scanned)
            or re.search(
                r"\b(?:sk-|oc_sk_|tvly-|np_)[A-Za-z0-9_-]{12,}|密码\s*[:：=]",
                scanned,
            )
        ):
            raise MemoryFailure("memory_credentials_forbidden")
        sensitive = bool(
            request.get("sensitive")
            or sensitive_text(text)
            or app_data is not None
            or typed_data is not None
        )
        if (
            action in {"save", "propose", "correct"}
            and sensitive
            and not request.get("confirmed_sensitive")
            and not self.assistant_automatic
        ):
            raise MemoryFailure("memory_confirmation_required")
        initial_version = own.version
        if action == "automatic_sharing":
            if not self.native_management_grant or self.project_id:
                raise MemoryFailure("memory_not_allowed")
            if request["expected_version"] != initial_version:
                raise MemoryFailure("memory_version_conflict")
            own.native_use_grant = (
                dict(self.native_management_grant) if request["enabled"] else None
            )
            result = {"automatic_native_use": request["enabled"], "version": initial_version + 1}
        elif action == "settings":
            if request["expected_version"] != initial_version:
                raise MemoryFailure("memory_version_conflict")
            own.enabled = request["enabled"]
            result = {
                "enabled": parent.enabled and own.enabled,
                "version": initial_version + 1,
                "mode": ("inherit" if own.enabled else "disabled") if self.project_id else "entry",
            }
        elif action in {"save", "propose"}:
            count = await self.db.scalar(
                select(func.count())
                .select_from(UserMemoryEntry)
                .where(
                    UserMemoryEntry.scope_id == own.scope_id,
                    UserMemoryEntry.status != "forgotten",
                )
            )
            if count >= 200:
                raise MemoryFailure("memory_scope_capacity")
            row = UserMemoryEntry(
                entry_id=new_id(),
                scope_id=own.scope_id,
                version=1,
                status="candidate" if action == "propose" else "active",
                sensitive=sensitive,
                body_uri=await self.put(
                    {
                        "text": text,
                        **({"app_data": app_data} if app_data else {}),
                        **({"typed_data": typed_data} if typed_data else {}),
                    }
                ),
                source_run_id=self.run_id,
                memory_type=typed_data["memory_type"] if typed_data else "free_text",
                schema_version=1,
                origin_owner_user_id=self.owner,
                origin_entry=self.entry,
                origin_scope_key=self.scope_key,
            )
            self.db.add(row)
            await self.db.flush()
            await self.retain_version(row, initial=True)
            await self.retain_version(row)
            result = {"entry_id": row.entry_id, "version": 1, "status": row.status}
        else:
            row = await self.db.scalar(
                select(UserMemoryEntry)
                .where(
                    UserMemoryEntry.entry_id == request.get("entry_id"),
                    UserMemoryEntry.scope_id == own.scope_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None or (row.status == "forgotten" and action != "undo"):
                raise MemoryFailure("memory_not_found")
            if row.version != request.get("expected_version"):
                raise MemoryFailure("memory_version_conflict")
            await self.retain_version(row)
            if action == "correct":
                if (
                    row.sensitive
                    and not request.get("confirmed_sensitive")
                    and not self.assistant_automatic
                ):
                    raise MemoryFailure("memory_confirmation_required")
                body = await self.read(row.body_uri)
                body["text"] = text
                if app_data is not None:
                    body["app_data"] = app_data
                if typed_data is not None:
                    body["typed_data"] = typed_data
                    row.memory_type = typed_data["memory_type"]
                row.body_uri = await self.put(body)
                row.sensitive = row.sensitive or sensitive
                row.disclosure_run_id = None
            elif action == "undo":
                prior = await self.db.get(UserMemoryVersion, (row.entry_id, row.version - 1))
                if prior is None:
                    raise MemoryFailure("memory_history_unavailable")
                if prior.status == "active" and not (parent.enabled and own.enabled):
                    raise MemoryFailure("memory_disabled")
                if prior.body_uri:
                    await self.read(prior.body_uri)
                for field in ("status", "sensitive", "body_uri", "memory_type", "schema_version"):
                    setattr(row, field, getattr(prior, field))
                row.disclosure_run_id = None
            elif action == "forget":
                row.status, row.body_uri, row.disclosure_run_id = "forgotten", None, None
            elif action == "confirm":
                if row.status != "candidate":
                    raise MemoryFailure("memory_not_candidate")
                if row.sensitive and not request.get("confirmed_sensitive"):
                    raise MemoryFailure("memory_confirmation_required")
                row.status = "active"
            elif action == "disclose":
                if not request.get("confirmed_sensitive") or not self.run_id:
                    raise MemoryFailure("memory_confirmation_required")
                grant = await self.db.scalar(
                    select(UserMemoryDisclosure).where(
                        UserMemoryDisclosure.entry_id == row.entry_id,
                        UserMemoryDisclosure.entry_version == row.version,
                        UserMemoryDisclosure.run_id == self.run_id,
                        UserMemoryDisclosure.recipient_entry == self.entry,
                    )
                )
                if grant is None:
                    self.db.add(
                        UserMemoryDisclosure(
                            entry_id=row.entry_id,
                            entry_version=row.version,
                            run_id=self.run_id,
                            recipient_entry=self.entry,
                            allowed_fields_json=["text", "app_data", "typed_data"],
                            confirmed_at=utc_now(),
                            request_key=request["key"],
                        )
                    )
                elif grant.revoked_at is not None:
                    grant.revoked_at, grant.confirmed_at = None, utc_now()
            else:
                raise MemoryFailure("memory_invalid_operation")
            if action != "disclose":
                row.version += 1
                await self.retain_version(row)
            result = {"entry_id": row.entry_id, "version": row.version, "status": row.status}
        # A conditional revision catches stale SQLite writes as well as locking MySQL callers.
        if self.project_id:
            checked = await self.db.execute(
                update(UserMemoryScope)
                .where(
                    UserMemoryScope.scope_id == parent.scope_id,
                    UserMemoryScope.version == parent_version,
                )
                .values(version=parent_version + 1)
                .execution_options(synchronize_session=False)
            )
            if checked.rowcount != 1:
                raise MemoryFailure("memory_version_conflict")
        changed = await self.db.execute(
            update(UserMemoryScope)
            .where(
                UserMemoryScope.scope_id == own.scope_id,
                UserMemoryScope.version == initial_version,
            )
            .values(version=initial_version + 1)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise MemoryFailure("memory_version_conflict")
        if result.get("entry_id"):
            result["applied_version"] = result["version"]
        self.db.add(
            UserMemoryOperation(
                scope_id=own.scope_id,
                request_key=request["key"],
                request_uri=await self.put(request),
                result_json=result,
            )
        )
        if self.require_running:
            from nexuspilot_api.models import LlmRun

            run = await self.db.scalar(
                select(LlmRun)
                .where(
                    LlmRun.run_id == self.run_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if run is None or run.status.value not in {"pending", "running"}:
                raise MemoryFailure("memory_run_cancelled")
        await self.db.commit()
        return result


async def memory_for_run(db, storage, run_id, scope="personal"):
    """Derive memory identity from a stored Run and revalidate its current App mapping."""
    from nexuspilot_api.features.identity.models.applications import (
        Application,
        ApplicationUserMapping,
        RunApplicationContext,
    )
    from nexuspilot_api.models import LlmRun

    run = await db.get(LlmRun, run_id, populate_existing=True)
    if run is None:
        raise MemoryFailure("memory_not_allowed")
    context = await db.get(RunApplicationContext, run_id, populate_existing=True)
    entry = "native"
    if context:
        mapping = await db.get(ApplicationUserMapping, context.mapping_id, populate_existing=True)
        application = await db.get(Application, mapping.application_id) if mapping else None
        if (
            mapping is None
            or not mapping.is_enabled
            or mapping.pilot_principal_id != run.user_id
            or application is None
            or not application.is_enabled
        ):
            raise MemoryFailure("memory_not_allowed")
        entry = mapping.application_id
    if scope == "project" and not run.project_id:
        raise MemoryFailure("memory_project_required")
    service = MemoryService(
        db, storage, run.user_id, entry, run.project_id if scope == "project" else None, run_id
    )
    await service.authorize()
    return service
