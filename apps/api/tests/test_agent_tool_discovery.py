"""Exercise discovery and loading through persisted Agent calls, not direct catalog mocks."""

import json

from nexuspilot_models.contracts import ModelResponse, ToolCall
from test_agent_file_tools import FileToolProvider
from test_agent_workflows import (
    register_default_agent_test_model,  # noqa: F401 — shared model catalog fixture
    scripted_registry,
    workflow_payload,
)
from test_conversation_file_context import prepare_conversation_file

from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app


class DiscoveryProvider(FileToolProvider):
    """Follow explicit tool batches and inspect schemas at every real Gateway round."""

    def __init__(self, batches):
        """Keep deterministic call batches separate from actual persisted tool results."""
        super().__init__()
        self.batches = batches
        self.worker_round = 0

    async def generate(self, request):
        """Emit search/describe/read turns, then produce the usual validated worker result."""
        if request.metadata["agent_node_key"] == "worker_execution":
            index = self.worker_round
            self.worker_round += 1
            if index < len(self.batches):
                self.requests.append(request)
                return ModelResponse(
                    tool_calls=self.batches[index],
                    finish_reason="tool_calls",
                    input_tokens=20,
                    output_tokens=10,
                    latency_ms=1,
                    raw_response={"fixture": "discovery"},
                )
        response = await super().generate(request)
        if request.metadata["agent_node_key"] == "controller_planning":
            response.structured_output["tasks"][0]["max_model_calls"] = len(self.batches) + 1
            response = response.model_copy(update={"text": json.dumps(response.structured_output)})
        return response


async def test_search_describe_load_and_read_retains_exact_schema_versions(client):
    """Search does not load; a committed description loads its pinned schema next round only."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = DiscoveryProvider(
        [
            [ToolCall(id="search", name="search_tools", arguments={"query": "结构"})],
            [
                ToolCall(
                    id="describe",
                    name="describe_tools",
                    arguments={"name": "get_file_outline", "version": "1.0.0"},
                )
            ],
            [
                ToolCall(
                    id="outline", name="get_file_outline", arguments={"file_id": file["file_id"]}
                )
            ],
        ]
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result
    rounds = [r for r in provider.requests if r.metadata["agent_node_key"] == "worker_execution"]
    assert "get_file_outline" not in {t.name for t in rounds[0].tools}
    assert "get_file_outline" not in {t.name for t in rounds[1].tools}
    loaded = next(t for t in rounds[2].tools if t.name == "get_file_outline")
    description = json.loads(rounds[2].messages[-1].content)["result"]
    assert loaded.input_schema == description["input_schema"]
    assert description["version"] == "1.0.0"
    assert json.loads(rounds[2].metadata["loaded_tool_versions"])["get_file_outline"] == "1.0.0"
    assert "get_file_outline" not in json.loads(rounds[1].metadata["loaded_tool_versions"])
    search = json.loads(rounds[1].messages[-1].content)["result"]
    assert "get_file_outline" in {item["name"] for item in search["items"]}
    worker = next(n for n in result["nodes"] if n["output_type"] == "agent_model_execution")
    assert worker["usage"]["tool_call_count"] == 3
    assert worker["usage"]["model_call_count"] == 4


async def test_loading_does_not_authorize_sibling_call_until_next_round(client):
    """A hidden-schema call is denied, but the same arguments can succeed after loading changes."""
    _, _, file, turn = await prepare_conversation_file(client)
    provider = DiscoveryProvider(
        [
            [
                ToolCall(
                    id="describe",
                    name="describe_tools",
                    arguments={"name": "get_file_outline", "version": "1.0.0"},
                ),
                ToolCall(
                    id="early", name="get_file_outline", arguments={"file_id": file["file_id"]}
                ),
            ],
            [
                ToolCall(
                    id="loaded", name="get_file_outline", arguments={"file_id": file["file_id"]}
                )
            ],
        ]
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result
    rounds = [r for r in provider.requests if r.metadata["agent_node_key"] == "worker_execution"]
    early = json.loads(rounds[1].messages[-1].content)
    assert early["status"] == "permission_denied"
    assert early["error_code"] == "tool_not_loaded"
    assert json.loads(rounds[-1].messages[-1].content)["status"] == "completed"


async def test_unknown_version_is_not_loaded_or_silently_upgraded(client):
    """Describe returns a safe missing-version result, retaining the original loaded subset."""
    _, _, _, turn = await prepare_conversation_file(client)
    provider = DiscoveryProvider(
        [
            [
                ToolCall(
                    id="wrong",
                    name="describe_tools",
                    arguments={"name": "get_file_outline", "version": "9.0.0"},
                )
            ],
        ]
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "completed", result
    rounds = [r for r in provider.requests if r.metadata["agent_node_key"] == "worker_execution"]
    assert "get_file_outline" not in {t.name for t in rounds[-1].tools}
    assert json.loads(rounds[-1].messages[-1].content)["error_code"] == "tool_unavailable"


async def test_removing_described_version_stops_before_next_provider_request(client, monkeypatch):
    """A removed version is not resent from history or silently replaced after describing it."""
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService
    from nexuspilot_api.services.tool_catalog import ToolCatalog

    _, _, file, turn = await prepare_conversation_file(client)
    original = AgentFileToolService.execute

    async def remove_after_description(service, **kwargs):
        """Simulate server-side removal after the description has been durably recorded."""
        record = await original(service, **kwargs)
        service.catalog = ToolCatalog(
            tuple(entry for entry in service.catalog.entries if entry.name != "get_file_outline")
        )
        return record

    monkeypatch.setattr(AgentFileToolService, "execute", remove_after_description)
    provider = DiscoveryProvider(
        [
            [
                ToolCall(
                    id="describe",
                    name="describe_tools",
                    arguments={"name": "get_file_outline", "version": "1.0.0"},
                )
            ],
            [ToolCall(id="read", name="get_file_outline", arguments={"file_id": file["file_id"]})],
        ]
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{turn['run']['run_id']}/agent-workflows",
            json=workflow_payload(execution_profile="tool_enabled_v1"),
        )
    ).json()
    assert result["status"] == "failed", result
    assert (
        len([r for r in provider.requests if r.metadata["agent_node_key"] == "worker_execution"])
        == 1
    )
    worker = next(n for n in result["nodes"] if n["output_type"] == "agent_model_execution")
    assert worker["evidence"]["tool_results"][0]["tool_name"] == "describe_tools"
    assert worker["evidence"]["tool_results"][0]["status"] == "completed"
