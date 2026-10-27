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
@pytest.mark.parametrize("action", ["save", "query"])
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
    if action == "query":
        saved = await client.post(
            path, json={"action": "save", "text": "我喜欢简洁的中文回答", "key": "seed"}
        )
        assert saved.status_code == 200, saved.text

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
                            name=f"{action}_memory",
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
    assert (await client.get(path, headers=headers, params={"scope": "project"})).status_code == 409
    assert (await client.get(path)).status_code == 401
