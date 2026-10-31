"""Exercise scoped product memory without models, network or existing experimental hashes."""

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from test_agent_workflows import register_default_agent_test_model  # noqa: F401
from test_api import create_test_run
from test_general_agent import general_model_catalog  # noqa: F401

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure, MemoryService
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app


async def test_typed_memory_remains_encrypted_and_requires_type_confirmation(
    resource_client, memory_key, test_database_session_factory,
):
    """Typed places require explicit consent and survive text-only corrections without SQL prose."""
    from nexuspilot_api.features.memory.models.product_memory import UserMemoryEntry
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run

    run = await native_run(resource_client, "管理记忆")
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        service = await memory_for_run(db, storage, run["run_id"])
        data = {"memory_type": "place", "label": "home", "address": "synthetic address",
                "coordinate_system": "GCJ02", "latitude": 24.0, "longitude": 109.0}
        request = {"action": "save", "key": "typed-place", "text": "My place",
                   "typed_data": data, "confirmed_sensitive": True}
        with pytest.raises(MemoryFailure, match="memory_type_confirmation_required"):
            await service.mutate(request)
        saved = await service.mutate({**request, "confirmed_type": True})
        row = await db.get(UserMemoryEntry, saved["entry_id"])
        assert row.memory_type == "place" and row.origin_entry == "native"
        assert row.origin_owner_user_id == run["user_id"]
        assert (await service.query())["items"][0]["typed_data"]["address"] == data["address"]
        assert "typed_data" not in (await service.query(for_model=True))["items"][0]
        await service.mutate({"action": "correct", "key": "typed-correction",
            "entry_id": row.entry_id, "expected_version": 1, "text": "Updated label",
            "confirmed_sensitive": True})
        assert (await service.query())["items"][0]["typed_data"]["address"] == data["address"]


@pytest.mark.parametrize("prompt", ["记住我喜欢中文，保存后确认一下", "记住我喜欢中文"])
async def test_reworded_save_stays_candidate_until_user_confirmation(
    resource_client, memory_key, test_database_session_factory, prompt
):
    """A rewritten model save becomes a stable candidate, not consent to activate it."""
    from types import SimpleNamespace

    from nexuspilot_api.features.memory.services.memory_tools import SaveArguments, execute_memory
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run

    run = await native_run(resource_client, prompt)
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        tools = SimpleNamespace(db=db, storage=storage)
        attempt = SimpleNamespace(run_id=run["run_id"])
        record = SimpleNamespace(tool_call_id="rewritten-save")
        args = SaveArguments(text="我喜欢 中文")
        result = await execute_memory(tools, attempt, record, "save_memory", args)
        assert result["status"] == "candidate"
        assert await execute_memory(tools, attempt, record, "save_memory", args) == result
    async with test_database_session_factory() as db:
        service = await memory_for_run(db, storage, run["run_id"])
        assert (await service.query(automatic=True, for_model=True))["items"] == []
        confirmed = await service.mutate(
            {
                "action": "confirm",
                "entry_id": result["entry_id"],
                "expected_version": 1,
                "key": "explicit-confirm",
            }
        )
        assert confirmed["status"] == "active"


async def test_settings_management_without_run(
    resource_client, memory_key, test_database_session_factory
):
    """Personal settings operate as the authenticated owner without creating a synthetic Run."""
    from sqlalchemy import func, select

    from nexuspilot_api.models import LlmRun

    async with test_database_session_factory() as db:
        before = await db.scalar(select(func.count()).select_from(LlmRun))
    path = "/api/v1/me/user-memories"
    saved = await resource_client.post(
        path, json={"action": "save", "text": "简洁", "key": "settings-save"}
    )
    assert saved.status_code == 200, saved.text
    assert (await resource_client.get(path)).json()["items"][0]["text"] == "简洁"
    settings = (await resource_client.get("/api/v1/me/memory-settings")).json()
    closed = await resource_client.post(
        path,
        json={
            "action": "settings",
            "enabled": False,
            "expected_version": settings["version"],
            "key": "close",
        },
    )
    assert closed.status_code == 200 and not closed.json()["enabled"]
    assert (
        await resource_client.post(path, json={"action": "save", "text": "new", "key": "off"})
    ).status_code == 409
    assert (
        await resource_client.post(
            path, json={"action": "save", "text": "bad", "key": "owner", "user_id": "other"}
        )
    ).status_code in {404, 422}
    owner = (await resource_client.get("/api/v1/auth/me")).json()["user_id"]
    project = await resource_client.post(
        "/api/v1/projects", json={"owner_user_id": owner, "project_name": "No Run memory"}
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]
    base = f"/api/v1/projects/{project_id}"
    inherited = (await resource_client.get(base + "/memory-settings")).json()
    assert inherited["mode"] == "inherit" and not inherited["enabled"]
    assert (await resource_client.get(path, params={"project_id": project_id})).status_code == 422
    await resource_client.post(
        path,
        json={
            "action": "settings",
            "enabled": True,
            "expected_version": closed.json()["version"],
            "key": "reopen",
        },
    )
    project_saved = await resource_client.post(
        base + "/user-memories",
        json={"action": "save", "scope": "project", "text": "项目规则", "key": "project-save"},
    )
    assert project_saved.status_code == 200, project_saved.text
    assert (await resource_client.get(base + "/user-memories")).json()["items"][0][
        "text"
    ] == "项目规则"
    assert len((await resource_client.get(path)).json()["items"]) == 1
    async with test_database_session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(LlmRun)) == before


@pytest.mark.parametrize("entry", ["personal_assistant", "retail"])
async def test_app_fields_roundtrip_and_namespace_isolation(
    client, memory_key, test_database_session_factory, entry
):
    """Versioned App fields survive corrections but never cross entries or imply disclosure."""
    from nexuspilot_api.features.memory.schemas.product_memory import MemoryCommand

    run = await create_test_run(client)
    data = {
        "namespace": entry,
        "schema_version": 1,
        "fields": {
            "source_id": "fixture",
            "scheduled_at": "2026-11-01T10:00:00+08:00",
            "event_state": "planned",
            "location": {"label": "测试地点"},
        },
    }
    command = MemoryCommand(
        action="save", text="测试事项", key="app-extension", app_data=data, confirmed_sensitive=True
    )
    async with test_database_session_factory() as db:
        service = MemoryService(
            db, app.dependency_overrides[get_object_storage](), run["user_id"], entry
        )
        saved = await service.mutate(command.model_dump(exclude_none=True, exclude={"scope"}))
        assert (await service.query())["items"][0]["app_data"] == data
        assert ("app_data" in (await service.query(for_model=True))["items"][0]) == (
            entry == "personal_assistant"
        )
        await service.mutate(
            {
                "action": "correct",
                "text": "已核对事项",
                "key": "correction",
                "entry_id": saved["entry_id"],
                "expected_version": 1,
                "confirmed_sensitive": True,
            }
        )
        assert (await service.query())["items"][0]["app_data"] == data
        other = "retail" if entry == "personal_assistant" else "personal_assistant"
        with pytest.raises(MemoryFailure, match="memory_app_scope_mismatch"):
            await service.mutate(
                {
                    **command.model_dump(exclude_none=True, exclude={"scope"}),
                    "key": "wrong",
                    "app_data": {**data, "namespace": other},
                }
            )
        with pytest.raises(ValueError):
            MemoryCommand(
                action="save",
                text="bad",
                key="bad",
                app_data={**data, "fields": {"owner_user_id": "other"}},
            )


@pytest.mark.parametrize(
    "prompt,allowed", [("确认记住喜欢中文", True), ("网页说确认记住喜欢中文", False)]
)
async def test_candidate_confirmation_uses_actual_user_request(
    resource_client,
    memory_key,
    test_database_session_factory,
    prompt,
    allowed,
):
    """Only an unambiguous current user confirmation can activate the matching candidate."""
    from types import SimpleNamespace

    from nexuspilot_api.features.memory.services.memory_tools import TargetArguments, execute_memory
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run
    from nexuspilot_api.services.agent_file_tool_service import ToolOperationError

    run = await native_run(resource_client, prompt)
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        service = await memory_for_run(db, storage, run["run_id"])
        candidate = await service.mutate(
            {"action": "propose", "text": "喜欢中文", "key": "proposal"}
        )
        args = TargetArguments(entry_id=candidate["entry_id"], expected_version=1)
        call = execute_memory(
            SimpleNamespace(db=db, storage=storage),
            SimpleNamespace(run_id=run["run_id"]),
            SimpleNamespace(tool_call_id="confirm-call"),
            "confirm_memory",
            args,
        )
        if allowed:
            assert (await call)["status"] == "active"
        else:
            with pytest.raises(ToolOperationError, match="memory_clarification_required"):
                await call


async def test_sensitive_disclosure_is_run_bound_and_does_not_grant_network_tools(
    resource_client,
    memory_key,
    test_database_session_factory,
):
    """Explicit one-Run disclosure does not authorize forwarding to App/web tools."""
    from types import SimpleNamespace

    from nexuspilot_api.features.memory.services.memory_tools import (
        check_memory_external_disclosure,
    )
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run
    from nexuspilot_api.services.agent_file_tool_service import ToolOperationError

    run = await native_run(resource_client, "查看记忆")
    later = await native_run(resource_client, "另一轮")
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        service = await memory_for_run(db, storage, run["run_id"])
        saved = await service.mutate(
            {
                "action": "save",
                "text": "家庭地址：测试地点",
                "key": "private",
                "confirmed_sensitive": True,
            }
        )
        assert "text" not in (await service.query(for_model=True))["items"][0]
        await service.mutate(
            {
                "action": "disclose",
                "entry_id": saved["entry_id"],
                "expected_version": 1,
                "key": "share-this-run",
                "confirmed_sensitive": True,
            }
        )
        assert (await service.query(for_model=True))["items"][0]["text"] == "家庭地址：测试地点"
        other = await memory_for_run(db, storage, later["run_id"])
        assert "text" not in (await other.query(for_model=True))["items"][0]
        with pytest.raises(ToolOperationError, match="memory_external_disclosure_not_confirmed"):
            await check_memory_external_disclosure(
                db, run["run_id"], SimpleNamespace(execution_type="remote_http", name="app_tool")
            )


async def test_disclosures_for_two_runs_coexist_until_content_changes(
    resource_client, memory_key, test_database_session_factory,
):
    """Granting a second Run preserves the first grant; correcting content invalidates both."""
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run

    runs = [await native_run(resource_client, "查看记忆") for _ in range(2)]
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        services = [await memory_for_run(db, storage, run["run_id"]) for run in runs]
        saved = await services[0].mutate({"action": "save", "text": "家庭地址：测试地点",
            "key": "two-run-save", "confirmed_sensitive": True})
        for index, service in enumerate(services):
            await service.mutate({"action": "disclose", "entry_id": saved["entry_id"],
                "expected_version": saved["version"], "key": f"two-run-disclose-{index}",
                "confirmed_sensitive": True})
        for service in services:
            assert (await service.query(for_model=True))["items"][0]["text"] == "家庭地址：测试地点"
        await services[0].mutate({"action": "correct", "entry_id": saved["entry_id"],
            "expected_version": saved["version"], "key": "two-run-correction",
            "text": "家庭地址：已纠正", "confirmed_sensitive": True})
        for service in services:
            assert "text" not in (await service.query(for_model=True))["items"][0]


async def test_memory_migration_preserves_existing_run(client, test_database_session_factory):
    """Exercise additive migration on the disposable DB; historical Runs receive no new grants."""
    import importlib.util
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import text

    run = await create_test_run(client)
    path = Path(__file__).parents[1] / "migrations/versions/20261027_0030_product_memory.py"
    spec = importlib.util.spec_from_file_location("product_memory_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def cycle(connection):
        """Remove only empty test memory metadata then apply the actual upgrade."""
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            migration.upgrade()

    async with test_database_session_factory() as db:
        connection = await db.connection()
        await connection.run_sync(cycle)
        assert await db.scalar(text("SELECT count(*) FROM memory_run_permissions")) == 0
        assert (
            await db.scalar(
                text("SELECT count(*) FROM llm_runs WHERE run_id=:run"), {"run": run["run_id"]}
            )
            == 1
        )
        await db.commit()


async def test_settings_retry_reports_current_state(
    client, memory_key, test_database_session_factory
):
    """Replaying an old enable operation must not report enabled after a later disable."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        service = MemoryService(
            db, app.dependency_overrides[get_object_storage](), run["user_id"], "native"
        )
        original = {"action": "settings", "enabled": True, "expected_version": 0, "key": "on"}
        await service.mutate(original)
        await service.mutate(
            {"action": "settings", "enabled": False, "expected_version": 1, "key": "off"}
        )
        replay = await service.mutate(original)
        assert replay["enabled"] is False
        assert replay["version"] == 2


async def test_cancel_during_object_write_does_not_publish_memory(
    resource_client,
    memory_key,
    test_database_session_factory,
):
    """Cancellation before commit rolls back metadata even if an encrypted object was written."""
    from sqlalchemy import select

    from nexuspilot_api.features.memory.models.product_memory import UserMemoryEntry
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run
    from nexuspilot_api.models import LlmRun

    run = await native_run(resource_client, "记住简洁回答")
    storage = app.dependency_overrides[get_object_storage]()

    class CancellingStorage:
        """Inject a persisted cancellation after the first real/fake object write."""

        cancelled = False

        async def put_bytes(self, *args):
            """Cancel through a separate transaction while memory is awaiting storage."""
            stored = await storage.put_bytes(*args)
            if self.cancelled:
                return stored
            self.cancelled = True
            async with test_database_session_factory() as cancel_db:
                target = await cancel_db.get(LlmRun, run["run_id"])
                target.status = type(target.status)("cancelled")
                await cancel_db.commit()
            return stored

    async with test_database_session_factory() as db:
        service = await memory_for_run(db, CancellingStorage(), run["run_id"])
        service.require_running = True
        with pytest.raises(MemoryFailure, match="memory_run_cancelled"):
            await service.mutate({"action": "save", "text": "简洁回答", "key": "cancelled-write"})
        assert list(await db.scalars(select(UserMemoryEntry))) == []


async def test_restored_history_rechecks_forgotten_records(
    resource_client,
    memory_key,
    test_database_session_factory,
):
    """Restore current sources without resurrecting forgotten text or duplicating saves."""
    import json
    from types import SimpleNamespace

    from nexuspilot_models.contracts import Message, MessageRole

    from nexuspilot_api.features.memory.services.memory_tools import (
        memory_checkpoint_message,
        sanitize_memory_history,
    )
    from nexuspilot_api.features.memory.services.product_memory import memory_for_run

    run = await native_run(resource_client, "记住简洁回答")
    storage = app.dependency_overrides[get_object_storage]()
    command = {"action": "save", "text": "简洁回答", "key": "stable-tool-execution"}
    async with test_database_session_factory() as db:
        saved = await (await memory_for_run(db, storage, run["run_id"])).mutate(command)
    async with test_database_session_factory() as db:
        service = await memory_for_run(db, storage, run["run_id"])
        assert await service.mutate(command) == saved
        assert len((await service.query())["items"]) == 1
        message = Message(
            role=MessageRole.TOOL,
            tool_call_id="call",
            tool_name="query_memory",
            content=json.dumps({"result": {"items": [{**saved, "text": "简洁回答"}]}}),
        )
        encoded = memory_checkpoint_message(message)
        assert "简洁回答" not in encoded["content"]
        restored = Message.model_validate(encoded)
        records = [
            SimpleNamespace(
                provider_tool_call_id="call",
                tool_name="query_memory",
                status="completed",
                input_json={},
            )
        ]
        tools = SimpleNamespace(db=db, storage=storage)
        await sanitize_memory_history(tools, run["run_id"], records, [restored])
        assert json.loads(restored.content)["result"]["items"][0]["text"] == "简洁回答"
        await db.commit()
        # Establish a worker-side read transaction before another request forgets the entry.
        await service.query()
        async with test_database_session_factory() as decision_db:
            decision = await memory_for_run(decision_db, storage, run["run_id"])
            await decision.mutate(
                {
                    "action": "forget",
                    "entry_id": saved["entry_id"],
                    "expected_version": 1,
                    "key": "forget",
                }
            )
        await sanitize_memory_history(tools, run["run_id"], records, [restored])
        assert json.loads(restored.content)["result"]["items"] == []
        await db.rollback()
        assert (await service.mutate(command))["status"] == "forgotten"


async def test_native_memory_cross_user_and_personal_key_permissions(
    resource_client,
    memory_key,
    test_database_session_factory,
    monkeypatch,
):
    """A genuine second login cannot manage another user's memory; execute Keys cannot write it."""
    from test_authentication import login, provision_user

    from nexuspilot_api.features.memory.models.product_memory import MemoryRunPermission

    monkeypatch.setattr(get_settings(), "authentication_enabled", True)
    await provision_user(test_database_session_factory)
    client = resource_client
    assert (await login(client)).status_code == 200
    csrf = {"Origin": "http://test", "X-CSRF-Token": client.cookies["nexuspilot_csrf"]}
    client.headers.update(csrf)
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    response = await client.post("/api/v1/runs", json={"user_id": owner, "user_request": "memory"})
    assert response.status_code == 201, response.text
    run_id = response.json()["run_id"]
    key = await client.post(
        "/api/v1/me/api-keys", json={"name": "memory-check", "scopes": ["read", "execute"]}
    )
    assert key.status_code == 201, key.text
    client.cookies.clear()
    client.headers.pop("X-API-Key", None)
    bearer = {"Authorization": "Bearer " + key.json()["key"]}
    from test_general_agent import general_payload

    started = await client.post(
        f"/api/v1/runs/{run_id}/agent-workflows", headers=bearer, json=general_payload()
    )
    assert started.status_code == 403, started.text
    assert (
        await client.post(
            f"/api/v1/runs/{run_id}/user-memories",
            headers=bearer,
            json={"action": "save", "text": "test", "key": "no"},
        )
    ).status_code == 403
    created = await client.post(
        "/api/v1/runs", headers=bearer, json={"user_id": owner, "user_request": "memory"}
    )
    assert created.status_code == 201, created.text
    async with test_database_session_factory() as db:
        assert not (await db.get(MemoryRunPermission, created.json()["run_id"])).allowed
    await provision_user(
        test_database_session_factory, user_id="memory-other", email="memory-other@example.com"
    )
    assert (await login(client, email="memory-other@example.com")).status_code == 200
    client.headers["X-API-Key"] = "test-api-key-long-enough"
    client.headers["X-CSRF-Token"] = client.cookies["nexuspilot_csrf"]
    denied = await client.get(f"/api/v1/runs/{run_id}/user-memories")
    assert denied.status_code == 404, denied.text


@pytest.fixture
def memory_key(monkeypatch):
    """Use an ephemeral test-only encryption key, never production configuration."""
    monkeypatch.setattr(
        get_settings(), "memory_encryption_key", SecretStr(Fernet.generate_key().decode())
    )


async def test_personal_memory_lifecycle(client, memory_key, test_database_session_factory):
    """Save/query/correct/forget with version checks; closing still permits management."""
    run = await create_test_run(client)
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        service = MemoryService(db, storage, run["user_id"], "native")
        assert (await service.settings())["enabled"] is True
        saved = await service.mutate({"action": "save", "text": "喜欢简洁中文", "key": "save-1"})
        assert saved["status"] == "active"
        assert (await service.query())["items"][0]["text"] == "喜欢简洁中文"
        assert (
            await service.mutate({"action": "save", "text": "喜欢简洁中文", "key": "save-1"})
            == saved
        )
        with pytest.raises(MemoryFailure):
            await service.mutate({"action": "save", "text": "different", "key": "save-1"})
        await service.mutate(
            {
                "action": "correct",
                "entry_id": saved["entry_id"],
                "expected_version": 1,
                "text": "喜欢英文",
                "key": "change",
            }
        )
        with pytest.raises(MemoryFailure):
            await service.mutate(
                {
                    "action": "correct",
                    "entry_id": saved["entry_id"],
                    "expected_version": 1,
                    "text": "旧版本",
                    "key": "stale",
                }
            )
        settings = await service.settings()
        await service.mutate(
            {
                "action": "settings",
                "enabled": False,
                "expected_version": settings["version"],
                "key": "off",
            }
        )
        assert (await service.query(automatic=True))["items"] == []
        assert len((await service.query())["items"]) == 1
        with pytest.raises(MemoryFailure):
            await service.mutate({"action": "save", "text": "新增", "key": "disabled"})
        await service.mutate(
            {
                "action": "forget",
                "entry_id": saved["entry_id"],
                "expected_version": 2,
                "key": "forget",
            }
        )
        assert (await service.query())["items"] == []


async def test_memory_entry_isolation(client, memory_key, test_database_session_factory):
    """Even identical owner identities do not share entries across application scopes."""
    run = await create_test_run(client)
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        first = MemoryService(db, storage, run["user_id"], "native")
        saved = await first.mutate({"action": "save", "text": "普通偏好", "key": "first"})
        second = MemoryService(db, storage, run["user_id"], "personal_assistant")
        assert (await second.query())["items"] == []
        with pytest.raises(MemoryFailure):
            await second.mutate(
                {
                    "action": "forget",
                    "entry_id": saved["entry_id"],
                    "expected_version": 1,
                    "key": "foreign",
                }
            )


async def test_sensitive_memory_requires_confirmation_and_disclosure(
    client,
    memory_key,
    test_database_session_factory,
):
    """Saving consent does not automatically authorize disclosure to a model."""
    run = await create_test_run(client)
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        service = MemoryService(db, storage, run["user_id"], "native")
        request = {
            "action": "save",
            "text": "家庭地址：某市某路1号",
            "sensitive": True,
            "key": "home",
        }
        with pytest.raises(MemoryFailure, match="confirmation_required"):
            await service.mutate(request)
        await service.mutate({**request, "confirmed_sensitive": True})
        assert "text" not in (await service.query(for_model=True))["items"][0]
        assert (await service.query())["items"][0]["text"] == request["text"]


async def native_run(client, text="记住我喜欢简洁的中文回答", project_id=None):
    """Create a Run through real local authentication, with no arbitrary owner override."""
    identity = (await client.get("/api/v1/auth/me")).json()
    owner = identity["user_id"]
    session = await client.post(
        "/api/v1/sessions",
        json={"user_id": owner, "title": "Memory fixture", "project_id": project_id},
    )
    assert session.status_code == 201, session.text
    response = await client.post(
        "/api/v1/runs",
        json={"user_id": owner, "user_request": text, "session_id": session.json()["session_id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_real_authorization_memory_routes(resource_client, memory_key):
    """Use actual native auth and HTTP validation, including forbidden identity parameters."""
    client = resource_client
    run = await native_run(client)
    path = f"/api/v1/runs/{run['run_id']}/user-memories"
    response = await client.post(
        path, json={"action": "save", "text": "喜欢简洁中文", "key": "one"}
    )
    assert response.status_code == 200, response.text
    entry = response.json()
    assert (await client.get(path)).json()["items"][0]["text"] == "喜欢简洁中文"
    denied = await client.post(
        path, json={"action": "save", "text": "其他", "key": "bad", "user_id": "other"}
    )
    assert denied.status_code in {404, 422}
    stale = await client.post(
        path,
        json={
            "action": "correct",
            "entry_id": entry["entry_id"],
            "expected_version": 9,
            "text": "其他",
            "key": "stale",
        },
    )
    assert stale.status_code == 409


async def test_nexus_project_inheritance_and_isolation(resource_client, memory_key):
    """A project inherits its entry setting, can close locally, and cannot read another project."""
    client = resource_client
    run = await native_run(client)
    projects = []
    for name in ("First", "Second"):
        response = await client.post(
            "/api/v1/projects", json={"owner_user_id": run["user_id"], "project_name": name}
        )
        assert response.status_code == 201, response.text
        projects.append(response.json()["project_id"])
    runs = [await native_run(client, project_id=project) for project in projects]
    path = f"/api/v1/runs/{runs[0]['run_id']}/user-memories"
    saved = await client.post(
        path,
        json={"action": "save", "scope": "project", "text": "项目用Python", "key": "project-save"},
    )
    assert saved.status_code == 200, saved.text
    other = f"/api/v1/runs/{runs[1]['run_id']}/user-memories"
    assert (await client.get(other, params={"scope": "project"})).json()["items"] == []
    assert (await client.get(path)).json()["items"] == []
    settings_path = f"/api/v1/runs/{runs[0]['run_id']}/memory-settings"
    setting = (await client.get(settings_path, params={"scope": "project"})).json()
    assert setting["mode"] == "inherit" and setting["enabled"]
    await client.post(
        path,
        json={
            "action": "settings",
            "scope": "project",
            "key": "close",
            "enabled": False,
            "expected_version": setting["version"],
        },
    )
    assert not (await client.get(settings_path, params={"scope": "project"})).json()["enabled"]
    assert (await client.get(settings_path)).json()["enabled"]
    parent = (await client.get(settings_path)).json()
    await client.post(
        path,
        json={
            "action": "settings",
            "key": "close-parent",
            "enabled": False,
            "expected_version": parent["version"],
        },
    )
    project_setting = (await client.get(settings_path, params={"scope": "project"})).json()
    await client.post(
        path,
        json={
            "action": "settings",
            "scope": "project",
            "key": "inherit",
            "enabled": True,
            "expected_version": project_setting["version"],
        },
    )
    assert not (await client.get(settings_path, params={"scope": "project"})).json()["enabled"]
    # Closed memories remain visible to explicit user management, not automatic injection.
    assert len((await client.get(path, params={"scope": "project"})).json()["items"]) == 1


@pytest.mark.parametrize("failure", ["key", "storage", "credentials"])
async def test_memory_failure_does_not_publish(
    client, monkeypatch, memory_key, test_database_session_factory, failure
):
    """No key/plaintext fallback or failed upload can create an active SQL record."""
    from sqlalchemy import select

    from nexuspilot_api.features.memory.models.product_memory import UserMemoryEntry

    run = await create_test_run(client)
    storage = app.dependency_overrides[get_object_storage]()
    if failure == "key":
        monkeypatch.setattr(get_settings(), "memory_encryption_key", None)
    if failure == "storage":

        async def fail(*args):
            """Fail only this test's object write without network access."""
            raise OSError("fixture")

        monkeypatch.setattr(storage, "put_bytes", fail)
    async with test_database_session_factory() as db:
        service = MemoryService(db, storage, run["user_id"], "native")
        with pytest.raises(MemoryFailure):
            await service.mutate(
                {
                    "action": "save",
                    "key": "fail",
                    "text": "password=not-for-memory" if failure == "credentials" else "偏好",
                }
            )
        assert list(await db.scalars(select(UserMemoryEntry))) == []


async def test_candidates_and_closed_management(client, memory_key, test_database_session_factory):
    """Candidates require a user decision; disabling does not revive forgotten entries."""
    run = await create_test_run(client)
    async with test_database_session_factory() as db:
        service = MemoryService(
            db, app.dependency_overrides[get_object_storage](), run["user_id"], "native"
        )
        candidate = await service.mutate(
            {"action": "propose", "text": "喜欢简短", "key": "candidate"}
        )
        assert (await service.query(for_model=True))["items"] == []
        setting = await service.settings()
        await service.mutate(
            {
                "action": "settings",
                "enabled": False,
                "key": "off",
                "expected_version": setting["version"],
            }
        )
        with pytest.raises(MemoryFailure, match="disabled"):
            await service.mutate(
                {
                    "action": "confirm",
                    "entry_id": candidate["entry_id"],
                    "expected_version": 1,
                    "key": "not-confirmed",
                }
            )
        await service.mutate(
            {
                "action": "forget",
                "entry_id": candidate["entry_id"],
                "expected_version": 1,
                "key": "forgot",
            }
        )
        setting = await service.settings()
        await service.mutate(
            {
                "action": "settings",
                "enabled": True,
                "key": "on",
                "expected_version": setting["version"],
            }
        )
        assert (await service.query())["items"] == []


@pytest.mark.parametrize("mode", ["quick", "agent"])
@pytest.mark.parametrize("action", ["save", "query", "disclosed_query"])
async def test_memory_tools_in_both_modes(resource_client, memory_key, mode, action):
    """Fixed models exercise actual tool persistence and final responses without paid requests."""
    import json

    from nexuspilot_models.contracts import ModelResponse, StreamEvent, StreamEventType, ToolCall
    from test_agent_workflows import scripted_registry
    from test_general_agent import GeneralProvider, general_payload

    from nexuspilot_api.core.dependencies import get_provider_registry

    client = resource_client
    text = "记住我喜欢简洁的中文回答" if action == "save" else "你记得我的偏好吗？"
    client._transport.raise_app_exceptions = True
    initial = await native_run(client, text)
    # The helper provisions a pending Run; finish that setup before submitting a new turn.
    assert (await client.post(f"/api/v1/runs/{initial['run_id']}/cancel")).status_code == 200
    turn = await client.post(
        f"/api/v1/sessions/{initial['session_id']}/turns",
        json={
            "user_id": initial["user_id"],
            "content_text": text,
            "idempotency_key": f"memory-{mode}-{action}",
        },
    )
    assert turn.status_code == 201, turn.text
    run_id = turn.json()["run"]["run_id"]
    path = f"/api/v1/runs/{run_id}/user-memories"
    if action in {"query", "disclosed_query"}:
        saved = await client.post(
            path,
            json={
                "action": "save",
                "text": "我喜欢简洁的中文回答",
                "key": "seed",
                "sensitive": action == "disclosed_query",
                "confirmed_sensitive": action == "disclosed_query",
            },
        )
        assert saved.status_code == 200, saved.text
        if action == "disclosed_query":
            selected = saved.json()
            command = {
                "action": "disclose",
                "scope": "personal",
                "entry_id": selected["entry_id"],
                "expected_version": selected["version"],
                "confirmed_sensitive": True,
                "key": f"disclose:{run_id}:{selected['entry_id']}:{selected['version']}",
            }
            granted = await client.post(path, json=command)
            assert granted.status_code == 200, granted.text
            # The browser may lose the first response; recovery uses the same selection/version.
            repeated = await client.post(path, json=command)
            assert repeated.status_code == 200 and repeated.json() == granted.json()

    class Provider(GeneralProvider):
        """Ask for one memory tool and verify the real returned result before answering."""

        async def generate(self, request):
            """Use advertised memory schemas and persisted results, never a mocked service."""
            self.requests.append(request)
            assert "query_memory" in {tool.name for tool in request.tools}
            previous = [m for m in request.messages if m.role.value == "tool"]
            if not previous:
                return ModelResponse(
                    tool_calls=[
                        ToolCall(
                            id="memory-call",
                            name="save_memory" if action == "save" else "query_memory",
                            arguments={"text": "我喜欢简洁的中文回答"} if action == "save" else {},
                        )
                    ],
                    finish_reason="tool_calls",
                    latency_ms=1,
                    raw_response={},
                )
            value = json.loads(previous[-1].content)
            assert value["status"] == "completed", value
            if action == "save":
                assert value["result"]["status"] == "active"
            else:
                assert value["result"]["items"][0]["text"] == "我喜欢简洁的中文回答"
            return ModelResponse(
                text="记忆操作结果已确认", finish_reason="stop", latency_ms=1, raw_response={}
            )

        async def stream(self, request):
            """Emit the same deterministic response through quick-reply streaming normalization."""
            response = await self.generate(request)
            yield StreamEvent(type=StreamEventType.STARTED, sequence=1)
            if response.text:
                yield StreamEvent(
                    type=StreamEventType.TEXT_DELTA, sequence=2, data={"delta": response.text}
                )
            yield StreamEvent(
                type=StreamEventType.COMPLETED,
                sequence=3,
                data={"response": response.model_dump(mode="json")},
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    if mode == "agent":
        response = await client.post(
            f"/api/v1/runs/{run_id}/agent-workflows", json=general_payload("never")
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "completed", response.text
    else:
        response = await client.post(
            "/api/v1/responses",
            json={
                "run_id": run_id,
                "current_user_message_id": turn.json()["message"]["message_id"],
                "provider": "openai",
                "model": "test-model",
                "input": text,
                "idempotency_key": "memory-round",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["output_text"] == "记忆操作结果已确认"
    assert len(provider.requests) == 2
    assert (await client.get(path)).json()["items"][0]["text"] == "我喜欢简洁的中文回答"


async def test_app_memory_isolation_and_project_rejection(resource_client, monkeypatch, memory_key):
    """Use the genuine App identity router; no remote HTTP callback or model is invoked."""
    import json

    configs = {}
    for name, tool in (
        ("personal_assistant", "personal_assistant_search_notes"),
        ("retail", "retail_search_products"),
    ):
        configs[name] = {
            "inbound_key": name + "-" + "x" * 32,
            "service_token": "fixture-" + "x" * 24,
            "base_url": "https://approved.example",
            "operations": {tool: "/tools"},
        }
    monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps(configs)))
    client = resource_client
    runs = []
    for name in configs:
        headers = {
            "Authorization": "Bearer " + configs[name]["inbound_key"],
            "X-Application-Id": name,
            "X-External-User-Id": "same-subject",
        }
        response = await client.post(
            "/api/v1/app/runs", headers=headers, json={"user_request": "memory fixture"}
        )
        assert response.status_code == 201, response.text
        runs.append((response.json(), headers))
    first, headers = runs[0]
    path = f"/api/v1/app/runs/{first['run_id']}/user-memories"
    saved = await client.post(
        path, headers=headers, json={"action": "save", "text": "中文偏好", "key": "one"}
    )
    assert saved.status_code == 200, saved.text
    assert (await client.get(path, headers=runs[1][1])).status_code == 404
    other_path = f"/api/v1/app/runs/{runs[1][0]['run_id']}/user-memories"
    assert (await client.get(other_path, headers=runs[1][1])).json()["items"] == []
    foreign = {**headers, "X-External-User-Id": "another-user"}
    assert (await client.get(path, headers=foreign)).status_code == 404
    management = "/api/v1/app/me/user-memories"
    managed = await client.get(management, headers=headers)
    assert managed.status_code == 200
    assert managed.json()["items"][0]["entry_id"] == saved.json()["entry_id"]
    assert (await client.get(management, headers=foreign)).json()["items"] == []
    assert (await client.get(management, headers=runs[1][1])).json()["items"] == []
    assert (await client.post(management, headers=headers, json={"action": "save",
        "text": "scope override", "key": "deny-project", "scope": "project"})).status_code == 409
    assert (await client.get(path, headers=headers, params={"scope": "project"})).status_code == 409
    assert (await client.get(path)).status_code == 401
    # Exercise the same public envelope for each App, not just the internal service method.
    for app_run, app_headers in runs:
        app_path = f"/api/v1/app/runs/{app_run['run_id']}/user-memories"
        extension = {
            "namespace": app_headers["X-Application-Id"],
            "schema_version": 1,
            "fields": {"source_id": "fixture", "event_state": "planned"},
        }
        payload = {
            "action": "save",
            "text": "扩展字段实验",
            "key": "app-fields",
            "app_data": extension,
        }
        assert (await client.post(app_path, headers=app_headers, json=payload)).status_code == 409
        response = await client.post(
            app_path, headers=app_headers, json={**payload, "confirmed_sensitive": True}
        )
        assert response.status_code == 200, response.text
        items = (await client.get(app_path, headers=app_headers)).json()["items"]
        assert (
            next(item for item in items if item["entry_id"] == response.json()["entry_id"])[
                "app_data"
            ]
            == extension
        )


async def test_explicit_grant_keyword_fallback_is_run_scoped(
    client, memory_key, test_database_session_factory
):
    """Native entry still requires version-bound Run grants; Assistant has its own recall policy."""
    run = await create_test_run(client)
    storage = app.dependency_overrides[get_object_storage]()
    async with test_database_session_factory() as db:
        service = MemoryService(
            db, storage, run["user_id"], "native", run_id=run["run_id"]
        )
        saved = await service.mutate(
            {
                "action": "save",
                "text": "同和佳境到龙城路通勤",
                "key": "fallback-save",
                "sensitive": True,
                "confirmed_sensitive": True,
            }
        )
        hidden = await service.query(for_model=True, query="完全不匹配")
        assert hidden["items"][0]["disclosure_required"] and "text" not in hidden["items"][0]
        await service.mutate(
            {
                "action": "disclose",
                "entry_id": saved["entry_id"],
                "expected_version": 1,
                "confirmed_sensitive": True,
                "key": "fallback-grant",
            }
        )
        found = await service.query(for_model=True, query="不存在的复合词 otherword")
        assert found["authorized_fallback"] and found["items"][0]["text"] == "同和佳境到龙城路通勤"
        assert (await service.query(for_model=True, query="不存在 通勤"))["items"][0][
            "entry_id"
        ] == saved["entry_id"]
        await service.mutate(
            {
                "action": "forget",
                "entry_id": saved["entry_id"],
                "expected_version": saved["version"],
                "key": "fallback-forget",
            }
        )
        assert (await service.query(for_model=True, query="不存在"))["items"] == []
