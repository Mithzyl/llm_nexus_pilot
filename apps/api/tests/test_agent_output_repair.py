"""Exercise bounded node-format repair through persisted workflow API calls."""

from datetime import timedelta

import pytest
from nexuspilot_models.contracts import ModelResponse, ToolCall
from sqlalchemy import select
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
    workflow_payload,
)
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.models import LlmModelToolCall


class RepairProvider(ScriptedAgentProvider):
    """Return a known malformed node once, or keep it invalid to test stopping."""

    def __init__(self, target, *, persistent=False, extra_status=False):
        """Select the node and reproducible defect without calling an external service."""
        super().__init__()
        self.target = target
        self.persistent = persistent
        self.extra_status = extra_status
        self.target_calls = 0

    def _node_output(self, node_key, request):
        """Omit a required field or add a platform-owned status to a valid fixture."""
        fixture_request = request.model_copy(
            update={"messages": [m for m in request.messages if m.role.value in {"system", "user"}]}
        )
        output = super()._node_output(node_key, fixture_request)
        if node_key == "controller_planning":
            for task in output["tasks"]:
                task["max_model_calls"] = 3 if self.target == "worker_execution" else 1
        if node_key == self.target:
            self.target_calls += 1
            if self.persistent or self.target_calls == 1:
                if self.extra_status:
                    output["status"] = "completed"
                elif node_key == "controller_planning":
                    for task in output["tasks"]:
                        task.pop("title")
                else:
                    output.pop(
                        {
                            "worker_execution": "summary",
                            "independent_review": "review_summary",
                            "final_synthesis": "final_text",
                        }[node_key]
                    )
        return output


def test_empty_node_response_cannot_enter_field_repair():
    """Even a custom provider reporting stop must not turn an absent answer into format repair."""
    from types import SimpleNamespace

    from nexuspilot_api.core.errors import InvalidRequestError
    from nexuspilot_api.features.agent_runtime.services.agent_model_node_service import (
        AgentModelNodeService,
    )

    with pytest.raises(InvalidRequestError, match="no output"):
        AgentModelNodeService._require_complete_response(
            SimpleNamespace(
                output_text=None, structured_output=None, tool_calls=[], finish_reason="stop"
            )
        )


@pytest.mark.parametrize(
    "target", ["controller_planning", "worker_execution", "independent_review", "final_synthesis"]
)
async def test_malformed_json_is_accounted_then_repaired_by_node(client, target):
    """An internal parse policy retains paid generation before the existing bounded node repair."""
    from nexuspilot_models.providers.common import parse_structured_output

    class MalformedProvider(ScriptedAgentProvider):
        """Return malformed text once at the selected node, using the actual shared JSON parser."""

        malformed_sent = False

        def _node_output(self, node_key, request):
            """Give the synthetic worker enough declared calls for the repair being tested."""
            output = super()._node_output(node_key, request)
            if node_key == "controller_planning" and target == "worker_execution":
                for task in output["tasks"]:
                    task["max_model_calls"] = 3
            return output

        async def generate(self, request):
            """Exercise the runtime's private validation policy before emitting a valid repair."""
            result = await super().generate(request)
            assert request._defer_structured_validation is True
            if request.metadata.get("agent_node_key") == target and not self.malformed_sent:
                self.malformed_sent = True
                text = '{"broken":'
                return result.model_copy(
                    update={
                        "text": text,
                        "structured_output": parse_structured_output(
                            text, defer_validation=request._defer_structured_validation
                        ),
                    }
                )
            return result

    run = await create_test_run(client)
    provider = MalformedProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result.get("error")
    node = next(n for n in result["nodes"] if n["node_key"].split(".")[0] == target)
    assert node["usage"]["model_call_count"] == 2
    assert len(node["evidence"]["model_attempt_ids"]) == 2


@pytest.mark.parametrize(
    "target", ["controller_planning", "worker_execution", "independent_review", "final_synthesis"]
)
async def test_invalid_node_is_repaired_once_with_full_usage(client, target):
    """Repair required fields without tools and retain both paid attempts in node totals."""
    run = await create_test_run(client)
    provider = RepairProvider(target)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(execution_profile="tool_enabled_v1"),
    )
    result = response.json()
    assert result["status"] == "completed", result.get("error")
    node = next(n for n in result["nodes"] if n["node_key"].split(".")[0] == target)
    assert node["usage"]["model_call_count"] == 2
    assert len(node["evidence"]["model_attempt_ids"]) == 2
    assert node["warnings"]
    assert provider.target_calls == 2
    repair = [r for r in provider.requests if r.metadata.get("node_output_repair") == "1"]
    assert len(repair) == 1
    assert repair[0].tools == []
    assert "validation_errors" in str(repair[0].messages)
    assert "Do not return the output_repair wrapper" in repair[0].system_instruction


async def test_worker_status_is_ignored_without_extra_model_call(client):
    """A model-owned status cannot control lifecycle or discard an otherwise valid result."""
    run = await create_test_run(client)
    provider = RepairProvider("worker_execution", extra_status=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result.get("error")
    assert provider.target_calls == 1
    worker = next(n for n in result["nodes"] if n["node_key"].startswith("worker_execution."))
    assert "status" not in worker["output"]["structured_output"]
    assert any("status" in warning for warning in worker["warnings"])


async def test_repeated_invalid_output_stops_after_one_repair(client):
    """A failed repair preserves diagnostics and attempts without an unbounded retry loop."""
    run = await create_test_run(client)
    provider = RepairProvider("controller_planning", persistent=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed"
    assert provider.target_calls == 2
    node = next(n for n in result["nodes"] if n["node_key"] == "controller_planning")
    assert node["usage"]["model_call_count"] == 2
    assert any("title" in warning and "missing" in warning for warning in node["warnings"])


class ToolRepairProvider(RepairProvider):
    """Read once, submit malformed output, then repair without any tool definitions."""

    async def generate(self, request):
        """Execute a read only in the initial round; the correction is a distinct request."""
        if request.metadata["agent_node_key"] == "worker_execution":
            if request.metadata.get("node_output_repair") == "1":
                assert not request.tools
            elif not any(m.role.value == "tool" for m in request.messages):
                self.requests.append(request)
                return ModelResponse(
                    tool_calls=[ToolCall(id="list-once", name="list_run_files", arguments={})],
                    finish_reason="tool_calls",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=1,
                    raw_response={"fixture": "list-once"},
                )
        return await super().generate(request)


async def test_repair_does_not_repeat_committed_tool_call(client, test_database_session_factory):
    """Count all three rounds but execute the persisted file-list operation only once."""
    run = await create_test_run(client)
    provider = ToolRepairProvider("worker_execution")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result.get("error")
    worker = next(n for n in result["nodes"] if n["node_key"].startswith("worker_execution."))
    assert worker["usage"]["model_call_count"] == 3
    assert worker["usage"]["tool_call_count"] == 1
    async with test_database_session_factory() as db:
        assert len(list(await db.scalars(select(LlmModelToolCall)))) == 1
    replay = (
        await client.get(f"/api/v1/agent-workflows/{result['workflow_execution_id']}/result")
    ).json()
    assert replay == result


async def test_repair_respects_workflow_call_budget(client):
    """Do not issue a repair when final synthesis used the last workflow allowance."""
    run = await create_test_run(client)
    provider = RepairProvider("final_synthesis")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(execution_profile="tool_enabled_v1")
    payload["max_model_calls"] = 4
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=payload,
        )
    ).json()
    assert result["status"] == "failed", result
    assert provider.target_calls == 1
    assert not any(r.metadata.get("node_output_repair") for r in provider.requests)


async def test_repair_respects_worker_call_budget(client):
    """Task limits include the correction even when the workflow has spare calls."""

    class OneCallWorkerProvider(RepairProvider):
        """Constrain the test task to a single model invocation."""

        def _node_output(self, node_key, request):
            """Override only the Controller's per-task allowance."""
            output = super()._node_output(node_key, request)
            if node_key == "controller_planning":
                output["tasks"][0]["max_model_calls"] = 1
            return output

    run = await create_test_run(client)
    provider = OneCallWorkerProvider("worker_execution")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed", result
    assert provider.target_calls == 1
    assert not any(r.metadata.get("node_output_repair") for r in provider.requests)


async def test_semantically_conflicting_review_is_not_format_repaired(client):
    """Do not turn a passing verdict with error findings into a format-only retry."""
    run = await create_test_run(client)
    provider = ScriptedAgentProvider(reviewer_error_finding=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed", result
    assert not any(r.metadata.get("node_output_repair") for r in provider.requests)


async def test_model_only_keeps_strict_output_behavior(client):
    """The legacy profile does not silently opt into the new tool-workflow normalization."""
    run = await create_test_run(client)
    provider = RepairProvider("final_synthesis", extra_status=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(),
        )
    ).json()
    assert result["status"] == "failed", result
    assert provider.target_calls == 1
    assert not any(r.metadata.get("node_output_repair") for r in provider.requests)


async def test_expired_node_does_not_get_new_timeout_for_repair(client, monkeypatch):
    """Simulate elapsed task time without sleeping or changing the workflow's state clock."""
    from nexuspilot_api.features.agent_runtime.services import agent_model_node_service
    from nexuspilot_api.models.base import utc_now

    monkeypatch.setattr(
        agent_model_node_service, "utc_now", lambda: utc_now() + timedelta(seconds=601)
    )
    run = await create_test_run(client)
    provider = RepairProvider("controller_planning")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed", result
    assert provider.target_calls == 1
    assert not any(r.metadata.get("node_output_repair") for r in provider.requests)


def test_diagnostics_do_not_expose_arbitrary_extra_keys_or_input_values():
    """Keep schema paths usable while preventing model-controlled keys from leaking as warnings."""
    from pydantic import ValidationError

    from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import AgentWorkerModelOutput
    from nexuspilot_api.features.agent_runtime.services.agent_model_node_service import (
        AgentModelNodeService,
    )

    with pytest.raises(ValidationError) as caught:
        AgentWorkerModelOutput.model_validate({"secret-file-content": "private-value"})
    errors = AgentModelNodeService._safe_validation_errors(AgentWorkerModelOutput, caught.value)
    assert {"loc": ["summary"], "type": "missing"} in errors
    assert {"loc": ["<unknown-field>"], "type": "extra_forbidden"} in errors
    assert "secret-file-content" not in str(errors)
    assert "private-value" not in str(errors)
