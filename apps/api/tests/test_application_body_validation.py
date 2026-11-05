"""Targeted caller schema, reviewer correction and creation-time connection tests only."""

import json

import pytest
from nexuspilot_models.contracts import ModelResponse
from pydantic import SecretStr
from test_agent_workflows import (
    ScriptedAgentProvider,
    register_default_agent_test_model,  # noqa: F401
    scripted_registry,
)  # noqa: F401
from test_application_body_processing import HEAD, INPUT, PROPOSAL
from test_application_body_processing import body_peer as caller_body_peer
from test_application_tool_loop import app_headers
from test_general_agent import (  # noqa: F401
    general_model_catalog,
    general_payload,
    unit_review_text,
)

from nexuspilot_api.core.config import get_settings
from nexuspilot_api.core.dependencies import get_provider_registry
from nexuspilot_api.main import app
from nexuspilot_api.services.application_body_processing import (
    matches_final_schema,
    parse_final_json,
    validate_final_schema,
)

body_peer = caller_body_peer


async def test_reviewer_revision_requires_new_platform_acceptance(resource_client, body_peer):
    """Return Reviewer corrections to the main Agent and reconfirm its new text with the caller."""

    class Provider(ScriptedAgentProvider):
        """Return one proposal, an initial answer, a reviewer correction and a revised answer."""

        def __init__(self):
            """Count main and reviewer invocations separately."""
            super().__init__()
            self.main_count = self.review_count = 0

        async def generate(self, request):
            """Make the first reviewer request a correction, then approve the corrected answer."""
            if request.metadata["agent_role"] == "reviewer":
                self.review_count += 1
                text = unit_review_text(
                    request,
                    "revise" if self.review_count == 1 else "supported",
                    "说明通知尚未接入" if self.review_count == 1 else "Checked",
                )
            else:
                self.main_count += 1
                text = (
                    PROPOSAL
                    if self.main_count == 1
                    else (
                        "已保存待办及提醒时间。"
                        if self.main_count == 2
                        else "已保存待办；通知尚未接入。"
                    )
                )
            return ModelResponse(
                text=text,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                finish_reason="stop",
                raw_response={},
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers("personal_assistant", "body-user")
    run = (
        await resource_client.post(
            "/api/v1/app/runs", headers=headers, json={"user_request": INPUT}
        )
    ).json()
    response = await resource_client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=headers,
        json=general_payload(
            "always",
            application_response_mode="caller_processed",
            application_protocol_instructions=HEAD,
        ),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert response.json()["final_output"]["final_text"] == "已保存待办；通知尚未接入。"
    assert provider.main_count == 3 and provider.review_count == 2
    assert [call["content"] for call in body_peer] == [
        PROPOSAL,
        "已保存待办及提醒时间。",
        "已保存待办；通知尚未接入。",
    ]
    assert len({call["workflow_id"] for call in body_peer}) == 1


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.test/schema"},
        {"type": []},
        {"type": "unsupported"},
        {"required": [1]},
        {"required": ["x", "x"]},
        {"properties": []},
        {"items": None},
        {"properties": {"x": None}},
        {"enum": []},
        {"additionalProperties": {}},
        {"type": None},
        {"required": None},
        {"enum": [float("nan")]},
    ],
)
def test_invalid_schema_is_rejected(schema):
    """Reject unsupported or malformed schema before any model or application request."""
    with pytest.raises(ValueError):
        validate_final_schema(schema)


@pytest.mark.parametrize(
    "value,schema,valid",
    [
        (
            {"x": "a"},
            {
                "type": "object",
                "required": ["x"],
                "properties": {"x": {"type": "string"}},
                "additionalProperties": False,
            },
            True,
        ),
        ({}, {"required": ["x"]}, False),
        ({"x": 1}, {"properties": {"x": {"type": "string"}}}, False),
        ({"extra": 1}, {"additionalProperties": False}, False),
        ([1, "x"], {"items": {"type": "integer"}}, False),
        (True, {"type": "integer"}, False),
        (True, {"enum": [1]}, False),
        ({"x": True}, {"enum": [{"x": 1}]}, False),
        (1.0, {"type": "integer"}, True),
        (float("inf"), {"type": "number"}, False),
        (None, {"type": "null"}, True),
    ],
)
def test_final_schema_supported_semantics(value, schema, valid):
    """Verify each supported keyword and JSON boolean/number distinctions."""
    assert matches_final_schema(value, schema) is valid


async def test_connection_is_pinned_before_first_call(
    resource_client, body_peer, monkeypatch, test_database_session_factory
):
    """Changing the approved destination after workflow creation cannot redirect the first body."""
    from nexuspilot_api.core.errors import InvalidRequestError
    from nexuspilot_api.services.application_body_processing import require_body_connection

    monkeypatch.setattr(get_settings(), "workflow_execution_backend", "worker")
    headers = app_headers("personal_assistant", "body-user")
    run = (
        await resource_client.post(
            "/api/v1/app/runs", headers=headers, json={"user_request": INPUT}
        )
    ).json()
    response = await resource_client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=headers,
        json=general_payload(
            "never",
            application_response_mode="caller_processed",
            application_protocol_instructions=HEAD,
        ),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"
    config = json.loads(get_settings().app_tools_json.get_secret_value())
    config["personal_assistant"]["response_processing_path"] = "/changed"
    monkeypatch.setattr(get_settings(), "app_tools_json", SecretStr(json.dumps(config)))
    async with test_database_session_factory() as db:
        with pytest.raises(InvalidRequestError, match="changed"):
            await require_body_connection(db, run["run_id"], require_binding=True)
    assert not body_peer


@pytest.mark.parametrize(
    "first", ["not json", "[]", "{}", '{"answer":1}', '{"answer":"ok","extra":1}', '{"answer":NaN}']
)
async def test_final_schema_error_is_corrected_in_original_agent(resource_client, body_peer, first):
    """Correct invalid final answers without saving them or appending warnings to their JSON."""

    class Provider(ScriptedAgentProvider):
        """Return invalid final structure once and then the schema-conforming replacement."""

        def __init__(self):
            """Count calls and keep the same saved task throughout correction."""
            super().__init__()
            self.calls = []

        async def generate(self, request):
            """Require the original draft and local validation error before correcting."""
            self.calls.append(request)
            if len(self.calls) > 1:
                assert any("final_schema_invalid" in (m.content or "") for m in request.messages)
                assert any(m.content == first for m in request.messages)
            return ModelResponse(
                text=first if len(self.calls) == 1 else '{"answer":"saved"}',
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
                finish_reason="stop",
                raw_response={},
            )

    provider = Provider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    headers = app_headers("personal_assistant", "body-user")
    run = (
        await resource_client.post(
            "/api/v1/app/runs", headers=headers, json={"user_request": INPUT}
        )
    ).json()
    response = await resource_client.post(
        f"/api/v1/app/runs/{run['run_id']}/agent-workflows",
        headers=headers,
        json=general_payload(
            "never",
            application_response_mode="caller_processed",
            application_protocol_instructions=HEAD,
            final_output_schema={
                "type": "object",
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert response.json()["final_output"]["final_text"] == '{"answer":"saved"}'
    assert len(provider.calls) == len(body_peer) == 2
    assert len({r["workflow_id"] for r in body_peer}) == 1
    assert provider.calls[0].system_instruction == provider.calls[1].system_instruction


def test_schema_size_and_depth_limits():
    """Reject oversized/deep schemas and unsupported property counts before invocation."""
    with pytest.raises(ValueError):
        validate_final_schema({"enum": ["x" * 16001]})
    with pytest.raises(ValueError):
        validate_final_schema({"properties": {str(i): {} for i in range(101)}})
    nested = {}
    for _ in range(10):
        nested = {"items": nested}
    with pytest.raises(ValueError):
        validate_final_schema(nested)


@pytest.mark.parametrize("text", ['{"nested":[NaN]}', '{"nested":Infinity}', '{"nested":1e999}'])
def test_non_json_numbers_are_rejected_even_without_property_schema(text):
    """Do not accept non-finite numbers hidden in fields outside the selected schema properties."""
    with pytest.raises(ValueError):
        parse_final_json(text)
