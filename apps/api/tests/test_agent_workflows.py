"""Phase 5 model-only Agent workflow API and persistence contract tests."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from nexuspilot_models.contracts import (
    ModelRequest,
    ModelResponse,
    ProviderName,
    TransportAttempt,
)
from nexuspilot_models.pricing import PriceCatalog
from nexuspilot_models.registry import ProviderRegistry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_api import create_test_run

from nexuspilot_api.core.dependencies import get_price_catalog, get_provider_registry
from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    AgentModelBinding,
    AgentWorkerModelOutput,
    AgentWorkflowCreate,
    AgentWorkflowNodeResultRead,
)
from nexuspilot_api.features.agent_runtime.services.workflow_definition_registry import (
    DEFAULT_WORKFLOW_DEFINITION_REGISTRY,
)
from nexuspilot_api.features.agent_runtime.services.workflow_execution_service import (
    AgentWorkflowExecutionService,
)
from nexuspilot_api.features.agent_runtime.services.workflow_execution_state_service import (
    WorkflowExecutionStateService,
)
from nexuspilot_api.features.agent_runtime.services.workflow_policy import (
    structured_model_instructions,
)
from nexuspilot_api.features.memory.schemas.collaboration_memory import (
    HANDOFF_V1_HARD_CAP_TOKENS,
)
from nexuspilot_api.infrastructure.object_storage import get_object_storage
from nexuspilot_api.main import app
from nexuspilot_api.models import (
    AgentRunStatus,
    AgentTurnStatus,
    AgentWorkflowNodeStatus,
    AgentWorkflowStatus,
    AttemptStatus,
    EvaluationVerdict,
    LlmAgentHandoff,
    LlmAgentRun,
    LlmAgentTurn,
    LlmAgentWorkflowEvent,
    LlmAgentWorkflowExecution,
    LlmAgentWorkflowNodeExecution,
    LlmMessage,
    LlmModelAttempt,
    LlmRun,
    LlmTask,
    LlmTaskEvaluation,
    MessageRole,
    TaskStatus,
)

INTERNAL_HEADERS = {"X-Internal-API-Key": "test-internal-key-long-enough"}


class ScriptedAgentProvider:
    """Return node-specific structured outputs for deterministic workflow tests."""

    name = ProviderName.OPENAI

    def __init__(
        self,
        *,
        forbidden_plan: bool = False,
        multi_task_plan: bool = False,
        independent_multi_task_plan: bool = False,
        invalid_worker_output: bool = False,
        reviewer_verdict: str = "pass",
        reviewer_requires_retry: bool = False,
        reviewer_error_finding: bool = False,
        oversized_final_output: bool = False,
        verbose_worker_output: bool = False,
        plan_review_policy: str | None = None,
        finish_reason: str = "stop",
    ) -> None:
        """Configure plan, output, review, and completion fixtures for one test."""

        self.forbidden_plan = forbidden_plan
        self.multi_task_plan = multi_task_plan
        self.independent_multi_task_plan = independent_multi_task_plan
        self.invalid_worker_output = invalid_worker_output
        self.reviewer_verdict = reviewer_verdict
        self.reviewer_requires_retry = reviewer_requires_retry
        self.reviewer_error_finding = reviewer_error_finding
        self.oversized_final_output = oversized_final_output
        self.verbose_worker_output = verbose_worker_output
        self.plan_review_policy = plan_review_policy
        self.finish_reason = finish_reason
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Return the typed fixture selected by the workflow node metadata."""

        self.requests.append(request)
        node_key = request.metadata["agent_node_key"]
        structured_output = self._node_output(node_key, request)
        return ModelResponse(
            text=json.dumps(structured_output),
            structured_output=structured_output,
            finish_reason=self.finish_reason,
            input_tokens=100,
            output_tokens=20,
            cached_tokens=10,
            latency_ms=5,
            provider_request_id=f"provider-{node_key}",
            raw_response={"node_key": node_key, "output": structured_output},
            transport_attempts=[TransportAttempt(attempt_index=1, status_code=200, latency_ms=5)],
        )

    async def stream(self, _request: ModelRequest):
        """Reject direct provider streaming because workflow SSE replays business events."""

        raise AssertionError("Agent workflow must not stream directly from the provider")
        yield

    def _node_output(self, node_key: str, request: ModelRequest) -> dict:
        """Build one complete structured output without hidden reasoning fields."""

        if node_key == "controller_planning":
            requested_review_policy = json.loads(request.messages[-1].content)["review_policy"]
            plan_review_policy = self.plan_review_policy or requested_review_policy
            if self.multi_task_plan or self.independent_multi_task_plan:
                dependencies = (
                    []
                    if self.independent_multi_task_plan
                    else [
                        {
                            "task_key": "plan-1",
                            "depends_on_task_key": "research-1",
                            "dependency_type": "completion",
                        }
                    ]
                )
                return {
                    "decision_summary": (
                        "Run two independent analyses."
                        if self.independent_multi_task_plan
                        else "Research first, then plan from that handoff."
                    ),
                    # Deliberately return the dependent Task first. The runtime
                    # must not use model-authored list order as dependency order.
                    "tasks": [
                        {
                            "task_key": "plan-1",
                            "task_type": "planning",
                            "title": "Plan from research",
                            "objective": "Use the research handoff to form a plan.",
                            "assigned_role": "researcher",
                            "required_capabilities": ["model_generation"],
                            "expected_output_type": "agent_handoff",
                            "completion_criteria": ["Reference the research handoff"],
                            "priority": 0,
                            "timeout_seconds": 60,
                            "max_model_calls": 1,
                        },
                        {
                            "task_key": "research-1",
                            "task_type": "analysis",
                            "title": "Research supplied text",
                            "objective": "Produce the upstream research marker.",
                            "assigned_role": "researcher",
                            "required_capabilities": ["model_generation"],
                            "expected_output_type": "agent_handoff",
                            "completion_criteria": ["Return the upstream marker"],
                            "priority": 0,
                            "timeout_seconds": 60,
                            "max_model_calls": 1,
                        },
                    ],
                    "dependencies": dependencies,
                    "review_policy": plan_review_policy,
                    "known_risks": [],
                    "unknowns": [],
                }
            role = "implementer" if self.forbidden_plan else "researcher"
            capabilities = ["write"] if self.forbidden_plan else ["model_generation"]
            return {
                "decision_summary": "Delegate the bounded analysis task.",
                "tasks": [
                    {
                        "task_key": "research-1",
                        "task_type": "analysis",
                        "title": "Analyze the supplied request",
                        "objective": "Produce an evidence-bounded analysis.",
                        "assigned_role": role,
                        "required_capabilities": capabilities,
                        "expected_output_type": "agent_handoff",
                        "completion_criteria": ["Return a non-empty summary"],
                        "priority": 0,
                        "timeout_seconds": 60,
                        "max_model_calls": 1,
                    }
                ],
                "dependencies": [],
                "review_policy": plan_review_policy,
                "known_risks": [],
                "unknowns": [],
            }
        if node_key.startswith("worker_execution"):
            if self.invalid_worker_output:
                return {
                    "confirmed_facts": [],
                    "decisions": [],
                    "remaining_work": [],
                    "risks": [],
                    "unknowns": [],
                }
            model_input = json.loads(request.messages[-1].content)
            task_objective = model_input["task"]["objective"]
            if self.verbose_worker_output:
                verbose_fact = "A detailed evidence statement. " * 40
                return {
                    "summary": f"completed:{task_objective}",
                    "confirmed_facts": [verbose_fact],
                    "decisions": [verbose_fact],
                    "remaining_work": [verbose_fact],
                    "risks": [verbose_fact],
                    "unknowns": [verbose_fact],
                }
            return {
                "summary": f"completed:{task_objective}",
                "confirmed_facts": ["Only explicit request content was used."],
                "decisions": ["Keep tool-dependent claims unverified."],
                "remaining_work": [],
                "risks": [],
                "unknowns": [],
            }
        if node_key == "independent_review":
            return {
                "verdict": self.reviewer_verdict,
                "score": "1.00" if self.reviewer_verdict == "pass" else "0.25",
                "findings": (
                    [
                        {
                            "finding_id": "review-error-1",
                            "severity": "error",
                            "category": "correctness",
                            "description": "The candidate has a blocking correctness defect.",
                            "evidence_refs": [],
                            "required_action": "Revise the candidate.",
                        }
                    ]
                    if self.reviewer_error_finding
                    else []
                ),
                "accepted_claims": ["The answer is bounded to supplied content."],
                "rejected_claims": [],
                "missing_evidence": [],
                "requires_replan": False,
                "requires_retry": self.reviewer_requires_retry,
                "review_summary": (
                    "The candidate satisfies the model-only contract."
                    if self.reviewer_verdict == "pass"
                    else "The candidate requires revision."
                ),
            }
        if node_key == "final_synthesis":
            return {
                "answer_type": "text",
                "final_text": "The model-only Agent workflow completed successfully.",
                "completed_objectives": ["Analyze the supplied request"],
                "unresolved_items": [],
                "warnings": (["x" * 2_000] * 40 if self.oversized_final_output else []),
                "recommended_next_actions": [],
            }
        raise AssertionError(f"Unexpected Agent node {node_key}")


class SlowWorkerProvider(ScriptedAgentProvider):
    """Ignore transport timing for one worker so the service deadline is exercised."""

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Delay only worker generation beyond its configured one-second timeout."""

        if request.metadata["agent_node_key"] == "worker_execution":
            await asyncio.sleep(1.2)
        return await super().generate(request)


class BlockingWorkerProvider(ScriptedAgentProvider):
    """Expose a controllable in-flight Worker request for cancellation tests."""

    def __init__(self) -> None:
        """Create a signal that fires after the Worker enters the provider adapter."""

        super().__init__()
        self.worker_request_started = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Block the Worker until its owning request task is externally cancelled."""

        if request.metadata["agent_node_key"] == "worker_execution":
            self.worker_request_started.set()
            await asyncio.Event().wait()
        return await super().generate(request)


class ConcurrentWorkerProvider(ScriptedAgentProvider):
    """Require two independent Worker calls to overlap before either can finish."""

    def __init__(self) -> None:
        """Prepare an independent two-task plan and a shared concurrency signal."""

        super().__init__(independent_multi_task_plan=True)
        self.started_worker_node_keys: list[str] = []
        self.all_workers_started = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Wait for both Worker calls, proving the runtime did not serialize them."""

        node_execution_key = request.metadata["agent_node_execution_key"]
        if node_execution_key.startswith("worker_execution."):
            self.started_worker_node_keys.append(node_execution_key)
            if len(self.started_worker_node_keys) == 2:
                self.all_workers_started.set()
            await asyncio.wait_for(self.all_workers_started.wait(), timeout=2)
        return await super().generate(request)


class ReleasableWorkerProvider(ScriptedAgentProvider):
    """Hold one Worker call while another request cancels its workflow."""

    def __init__(self) -> None:
        """Create start and release signals for an externally cancelled Provider call."""

        super().__init__()
        self.worker_request_started = asyncio.Event()
        self.release_worker_request = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Return the Worker result only after the cancellation endpoint is exercised."""

        if request.metadata["agent_node_execution_key"].startswith("worker_execution."):
            self.worker_request_started.set()
            await self.release_worker_request.wait()
        return await super().generate(request)


class CommittedNodeCheckingProvider(ScriptedAgentProvider):
    """Verify every billable model request starts after its node fact is committed."""

    def __init__(self, db_session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Bind an independent session factory used to observe committed node facts."""

        super().__init__()
        self.db_session_factory = db_session_factory
        self.committed_node_execution_ids: list[str] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Reject a provider call when its running node or started event is not durable."""

        node_execution_id = request.metadata["node_execution_id"]
        async with self.db_session_factory() as db_session:
            node = await db_session.get(LlmAgentWorkflowNodeExecution, node_execution_id)
            started_event = await db_session.scalar(
                select(LlmAgentWorkflowEvent).where(
                    LlmAgentWorkflowEvent.node_execution_id == node_execution_id,
                    LlmAgentWorkflowEvent.event_type == "agent.node.started",
                )
            )
        assert node is not None
        assert node.status == AgentWorkflowNodeStatus.RUNNING
        assert started_event is not None
        self.committed_node_execution_ids.append(node_execution_id)
        return await super().generate(request)


def scripted_registry(provider: ScriptedAgentProvider) -> ProviderRegistry:
    """Register one scripted provider under the existing allowed test model."""

    registry = ProviderRegistry()
    registry.register(
        ProviderName.OPENAI,
        provider,
        allowed_models=frozenset({"test-model"}),
    )
    return registry


def workflow_payload(**overrides: object) -> dict:
    """Return one valid model-only workflow request with explicit role bindings."""

    payload = {
        "workflow_name": "model_only",
        "workflow_version": "1.0.0",
        "execution_profile": "model_only_v1",
        "idempotency_key": "agent-workflow-request-0001",
        "review_policy": "always",
        "role_bindings": {
            "controller": {
                "provider": "openai",
                "model": "test-model",
                "max_output_tokens": 4_096,
            },
            "researcher": {
                "provider": "openai",
                "model": "test-model",
                "max_output_tokens": 4_096,
            },
            "reviewer": {
                "provider": "openai",
                "model": "test-model",
                "max_output_tokens": 4_096,
            },
        },
        "stream": False,
    }
    payload.update(overrides)
    return payload


def test_agent_model_binding_keeps_output_limit_optional() -> None:
    """Verify Agent output limits remain optional up to the Provider ceiling."""

    binding = AgentModelBinding.model_validate(
        {"provider": "openai", "model": "test-model"}
    )

    assert binding.max_output_tokens is None

    explicit_binding = AgentModelBinding.model_validate(
        {
            "provider": "openai",
            "model": "test-model",
            "max_output_tokens": 65_536,
        }
    )
    assert explicit_binding.max_output_tokens == 65_536

    with pytest.raises(ValueError):
        AgentModelBinding.model_validate(
            {
                "provider": "openai",
                "model": "test-model",
                "max_output_tokens": 65_537,
            }
        )


async def test_provider_is_called_only_after_node_started_fact_is_committed(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify independent sessions can read node start facts before Provider entry."""

    run = await create_test_run(client)
    provider = CommittedNodeCheckingProvider(test_database_session_factory)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-provider-after-commit-0001"),
    )

    assert response.status_code == 201, response.text
    assert len(provider.committed_node_execution_ids) == 4


async def test_workflow_event_sink_observes_only_committed_events(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify live observers are notified only after the same event is queryable."""

    run = await create_test_run(client)
    observed_event_ids: list[str] = []

    async def record_committed_event(event) -> None:
        """Assert a newly notified event is visible through an independent session."""

        async with test_database_session_factory() as observer_session:
            durable_event = await observer_session.get(LlmAgentWorkflowEvent, event.event_id)
        assert durable_event is not None
        assert durable_event.event_sequence == event.event_sequence
        observed_event_ids.append(event.event_id)

    async with test_database_session_factory() as db_session:
        service = AgentWorkflowExecutionService(
            registry=scripted_registry(ScriptedAgentProvider()),
            prices=app.dependency_overrides[get_price_catalog](),
            db_session=db_session,
            db_session_factory=test_database_session_factory,
            storage=app.dependency_overrides[get_object_storage](),
        )
        workflow, replayed = await service.prepare_workflow(
            run["run_id"],
            AgentWorkflowCreate.model_validate(
                workflow_payload(idempotency_key="agent-event-after-commit-0001")
            ),
            event_sink=record_committed_event,
        )

    assert replayed is False
    assert workflow.event_count == 1
    assert len(observed_event_ids) == 1


async def test_unbudgeted_workflow_does_not_apply_an_output_token_limit(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify an unbudgeted Agent workflow forwards no implicit output-token bound."""

    run = await create_test_run(client)
    async with test_database_session_factory() as db_session:
        durable_run = await db_session.get(LlmRun, run["run_id"])
        assert durable_run is not None
        durable_run.budget_limit = None
        await db_session.commit()
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(idempotency_key="agent-no-output-limit-0001")
    for binding in payload["role_bindings"].values():
        binding.pop("max_output_tokens")

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=payload,
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    assert provider.requests
    assert all(request.max_output_tokens is None for request in provider.requests)


async def test_prompted_json_workflow_requests_provider_json_object_mode(
    client: httpx.AsyncClient,
) -> None:
    """Verify prompted JSON uses Provider syntax enforcement without claiming JSON Schema."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(idempotency_key="agent-provider-json-object-0001")
    for binding in payload["role_bindings"].values():
        binding["structured_output_mode"] = "prompted_json"

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=payload,
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    assert provider.requests
    assert all(request.json_object_output for request in provider.requests)
    assert all(request.output_schema is None for request in provider.requests)


def test_prompted_json_instructions_distinguish_schema_rules_from_response_fields() -> None:
    """Verify every prompted-JSON Provider receives an explicit response-field allowlist."""

    instructions = structured_model_instructions(
        role="planner",
        purpose="Complete the assigned task.",
        output_model=AgentWorkerModelOutput,
        prompted_json=True,
    )

    assert (
        'Only these top-level response fields are allowed: "summary", '
        '"confirmed_facts", "decisions", "remaining_work", "risks", "unknowns".'
        in instructions
    )
    assert "Schema keywords are validation rules, not response fields." in instructions
    assert '"additionalProperties"' not in instructions


def test_model_instructions_allow_general_knowledge_without_claiming_external_evidence() -> None:
    """Verify ordinary Agent questions may use model knowledge without inventing evidence."""

    instructions = structured_model_instructions(
        role="planner",
        purpose="Answer an ordinary explanatory question.",
        output_model=AgentWorkerModelOutput,
        prompted_json=True,
    )

    assert "You may use general knowledge already available to the model" in instructions
    assert "Use only the supplied content" not in instructions
    assert "Never claim tools, files, tests, network requests, or external evidence" in instructions


async def test_budgeted_workflow_requires_an_explicit_output_bound(
    client: httpx.AsyncClient,
) -> None:
    """Verify cost reservation fails before Provider entry when no output bound exists."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(idempotency_key="agent-budget-output-bound-0001")
    for binding in payload["role_bindings"].values():
        binding.pop("max_output_tokens")

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=payload,
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["error_code"] == "agent_budget_output_limit_required"
    assert provider.requests == []


async def test_model_only_workflow_returns_and_persists_complete_nodes(
    client: httpx.AsyncClient,
) -> None:
    """Verify the primary Agent workflow completes with queryable full node results."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "completed", result
    assert result["execution_profile"] == "model_only_v1"
    assert result["active_node_execution_ids"] == []
    assert result["final_output"]["final_text"].startswith("The model-only")
    assert all(Decimal(node["budget"]["reserved_estimated_cost"]) == 0 for node in result["nodes"])
    assert [node["node_key"] for node in result["nodes"]] == [
        "request_intake",
        "context_assembly",
        "controller_planning",
        "plan_validation",
        "agent_dispatch",
        "worker_execution.research-1",
        "handoff_submission.research-1",
        "deterministic_verification",
        "independent_review",
        "final_synthesis",
        "workflow_completion",
    ]
    assert all(
        node["schema_version"] == "agent_workflow_node_result.v1" for node in result["nodes"]
    )
    assert all("input" in node and "evidence" in node for node in result["nodes"])
    verification_node = next(
        node for node in result["nodes"] if node["node_key"] == "deterministic_verification"
    )
    review_node = next(node for node in result["nodes"] if node["node_key"] == "independent_review")
    final_node = next(node for node in result["nodes"] if node["node_key"] == "final_synthesis")
    assert (
        verification_node["node_execution_id"] in review_node["input"]["source_node_execution_ids"]
    )
    assert {
        verification_node["node_execution_id"],
        review_node["node_execution_id"],
    }.issubset(set(final_node["input"]["source_node_execution_ids"]))
    assert "chain_of_thought" not in response.text
    assert len(provider.requests) == 4
    controller_request = next(
        request
        for request in provider.requests
        if request.metadata.get("agent_node_execution_key") == "controller_planning"
    )
    assert json.loads(controller_request.messages[-1].content)["allowed_roles"] == ["researcher"]

    workflow_id = result["workflow_execution_id"]
    summary = (await client.get(f"/api/v1/agent-workflows/{workflow_id}")).json()
    assert summary["status"] == "completed"
    assert summary["primary_node_execution_id"] is None
    assert summary["active_node_execution_ids"] == []
    assert summary["snapshot_version"] == result["snapshot_version"]
    discovered = (await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")).json()
    assert discovered == summary

    persisted_result = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/result")).json()
    assert persisted_result == result

    node_page = (await client.get(f"/api/v1/agent-workflows/{workflow_id}/nodes?limit=5")).json()
    assert len(node_page["items"]) == 5
    assert node_page["has_more"] is True
    first_node_id = node_page["items"][0]["node_execution_id"]
    node_detail = (
        await client.get(f"/api/v1/agent-workflows/{workflow_id}/nodes/{first_node_id}")
    ).json()
    assert node_detail == node_page["items"][0]


async def test_workflow_idempotency_replays_without_new_model_calls(
    client: httpx.AsyncClient,
) -> None:
    """Verify exact replay returns one workflow while changed input is rejected."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    url = f"/api/v1/runs/{run['run_id']}/agent-workflows"
    payload = workflow_payload(idempotency_key="agent-idempotency-0001")

    first = await client.post(url, json=payload)
    replay = await client.post(url, json=payload)
    conflict = await client.post(
        url,
        json={**payload, "review_policy": "never"},
    )
    different_key = await client.post(
        url,
        json={**payload, "idempotency_key": "agent-idempotency-0002"},
    )

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.json()["workflow_execution_id"] == first.json()["workflow_execution_id"]
    assert len(provider.requests) == 4
    assert conflict.status_code == 409
    assert different_key.status_code == 409


async def test_run_workflow_discovery_returns_not_found_before_creation(
    client: httpx.AsyncClient,
) -> None:
    """Verify a Run without an Agent workflow has an explicit discovery result."""

    run = await create_test_run(client)

    response = await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")

    assert response.status_code == 404


async def test_model_only_workflow_rejects_tool_or_implementer_plan(
    client: httpx.AsyncClient,
) -> None:
    """Verify phase 4 capabilities cannot be smuggled into the model-only profile."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(forbidden_plan=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-forbidden-plan-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "agent_capability_unavailable"
    validation = next(node for node in result["nodes"] if node["node_key"] == "plan_validation")
    assert validation["output"]["is_valid"] is False
    assert validation["output"]["capability_gaps"]
    assert len(provider.requests) == 1


async def test_workflow_events_are_ordered_and_replay_after_sequence(
    client: httpx.AsyncClient,
) -> None:
    """Verify persisted workflow SSE supports ordered recovery after disconnection."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    created = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-events-0001"),
    )
    workflow_id = created.json()["workflow_execution_id"]

    all_events_response = await client.get(f"/api/v1/agent-workflows/{workflow_id}/events")
    event_ids = [
        int(line.removeprefix("id: "))
        for line in all_events_response.text.splitlines()
        if line.startswith("id: ")
    ]
    replay_response = await client.get(
        f"/api/v1/agent-workflows/{workflow_id}/events?after_sequence={event_ids[-2]}"
    )
    replay_ids = [
        int(line.removeprefix("id: "))
        for line in replay_response.text.splitlines()
        if line.startswith("id: ")
    ]

    assert all_events_response.headers["content-type"].startswith("text/event-stream")
    assert event_ids == list(range(1, len(event_ids) + 1))
    assert replay_ids == [event_ids[-1]]
    assert "event: agent.workflow.completed" in replay_response.text


async def test_dependent_agent_receives_direct_handoff_and_evaluations_keep_ownership(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify dependency order also carries data and never reassigns Evaluation ownership."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(multi_task_plan=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-dependency-data-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "completed", result
    assert all(
        node["public_view"]["progress_current"] <= node["public_view"]["progress_total"]
        for node in result["nodes"]
    )
    dependent_request = next(
        request
        for request in provider.requests
        if request.metadata.get("agent_node_execution_key") == "worker_execution.plan-1"
    )
    dependent_input = json.loads(dependent_request.messages[-1].content)
    assert dependent_input["dependency_results"][0]["task_key"] == "research-1"
    assert dependent_input["dependency_results"][0]["worker_output"]["summary"].startswith(
        "completed:Produce the upstream"
    )
    assert dependent_input["dependency_results"][0]["handoff"]["agent_handoff_id"]

    upstream_handoff_node = next(
        node for node in result["nodes"] if node["node_key"] == "handoff_submission.research-1"
    )
    dependent_worker_node = next(
        node for node in result["nodes"] if node["node_key"] == "worker_execution.plan-1"
    )
    assert (
        upstream_handoff_node["node_execution_id"]
        in dependent_worker_node["input"]["source_node_execution_ids"]
    )
    assert (
        upstream_handoff_node["output"]["agent_handoff_id"]
        in dependent_worker_node["input"]["handoff_ids"]
    )

    final_request = next(
        request
        for request in provider.requests
        if request.metadata.get("agent_node_execution_key") == "final_synthesis"
    )
    final_input = json.loads(final_request.messages[-1].content)
    assert len(final_input["worker_results"]) == 2
    assert any(
        item["worker_output"]["summary"].startswith("completed:Produce the upstream")
        for item in final_input["worker_results"]
    )

    async with test_database_session_factory() as db_session:
        evaluations = list(
            (
                await db_session.scalars(
                    select(LlmTaskEvaluation).where(LlmTaskEvaluation.run_id == run["run_id"])
                )
            ).all()
        )
        attempts = {
            item.attempt_id: item
            for item in (
                await db_session.scalars(
                    select(LlmModelAttempt).where(LlmModelAttempt.run_id == run["run_id"])
                )
            ).all()
        }
        handoffs = {
            item.agent_handoff_id: item
            for item in (
                await db_session.scalars(
                    select(LlmAgentHandoff).where(LlmAgentHandoff.run_id == run["run_id"])
                )
            ).all()
        }

    worker_evaluations = [
        item
        for item in evaluations
        if item.evaluation_type in {"agent_deterministic_verification", "agent_independent_review"}
    ]
    assert len(worker_evaluations) == 4
    for evaluation in worker_evaluations:
        candidate_attempt = attempts[evaluation.candidate_attempt_id]
        assert evaluation.task_id == candidate_attempt.task_id
        handoff_id = evaluation.findings_json.get("handoff_id")
        if handoff_id is not None:
            assert handoffs[handoff_id].task_id == evaluation.task_id


async def test_invalid_worker_output_finalizes_all_started_facts_and_keeps_attempt_evidence(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a paid but contract-invalid model response leaves no false running state."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(invalid_worker_output=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-invalid-worker-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    failed_worker_node = next(
        node for node in result["nodes"] if node["node_key"].startswith("worker_execution")
    )
    assert failed_worker_node["status"] == "failed"
    assert failed_worker_node["evidence"]["model_attempt_ids"]
    assert failed_worker_node["usage"]["model_call_count"] == 1

    async with test_database_session_factory() as db_session:
        tasks = list(
            (await db_session.scalars(select(LlmTask).where(LlmTask.run_id == run["run_id"]))).all()
        )
        agent_runs = list(
            (
                await db_session.scalars(
                    select(LlmAgentRun).where(LlmAgentRun.run_id == run["run_id"])
                )
            ).all()
        )
        agent_turns = list((await db_session.scalars(select(LlmAgentTurn))).all())
        nodes = list(
            (
                await db_session.scalars(
                    select(LlmAgentWorkflowNodeExecution).where(
                        LlmAgentWorkflowNodeExecution.run_id == run["run_id"]
                    )
                )
            ).all()
        )
        attempts = list(
            (
                await db_session.scalars(
                    select(LlmModelAttempt).where(LlmModelAttempt.run_id == run["run_id"])
                )
            ).all()
        )

    assert tasks and all(task.status == TaskStatus.FAILED for task in tasks)
    assert agent_runs and all(item.status == AgentRunStatus.FAILED for item in agent_runs)
    assert agent_turns and all(item.status == AgentTurnStatus.FAILED for item in agent_turns)
    assert all(item.status != AgentWorkflowNodeStatus.RUNNING for item in nodes)
    invalid_attempt = next(
        item
        for item in attempts
        if item.attempt_id in failed_worker_node["evidence"]["model_attempt_ids"]
    )
    assert invalid_attempt.status == AttemptStatus.COMPLETED
    assert agent_turns[0].model_attempt_id == invalid_attempt.attempt_id


async def test_stream_creation_returns_committed_workflow_events(
    client: httpx.AsyncClient,
) -> None:
    """Verify POST SSE emits the committed start, node, and terminal workflow sequence."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-live-stream-0001", stream=True),
    )

    event_ids = [
        int(line.removeprefix("id: "))
        for line in response.text.splitlines()
        if line.startswith("id: ")
    ]
    assert response.status_code == 201
    assert response.headers["content-type"].startswith("text/event-stream")
    assert event_ids == list(range(1, len(event_ids) + 1))
    assert "event: agent.workflow.started" in response.text
    assert "event: agent.node.started" in response.text
    assert "event: agent.workflow.completed" in response.text


async def test_conditional_review_requires_reviewer_binding(client: httpx.AsyncClient) -> None:
    """Verify a request cannot defer a required Reviewer binding until runtime failure."""

    run = await create_test_run(client)
    payload = workflow_payload(review_policy="on_verification_failure")
    payload["role_bindings"].pop("reviewer")

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=payload,
    )

    assert response.status_code == 422


async def test_review_policy_rejects_insufficient_model_call_budget_before_provider(
    client: httpx.AsyncClient,
) -> None:
    """Verify required review work cannot exhaust the budget after a paid Controller call."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(max_model_calls=3),
    )

    assert response.status_code == 422
    assert provider.requests == []


async def test_exhausted_run_budget_returns_stable_workflow_error_before_provider(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify an exhausted monetary budget is not mislabeled as invalid node output."""

    run = await create_test_run(client)
    async with test_database_session_factory() as db_session:
        durable_run = await db_session.get(LlmRun, run["run_id"])
        assert durable_run is not None
        durable_run.budget_limit = 0
        await db_session.commit()
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-exhausted-budget-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "agent_budget_exhausted"
    assert provider.requests == []


async def test_controller_cannot_change_request_review_policy(
    client: httpx.AsyncClient,
) -> None:
    """Verify a model-authored review policy cannot override the caller's workflow policy."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(plan_review_policy="always")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(
            idempotency_key="agent-review-policy-mismatch-0001",
            review_policy="never",
        ),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "agent_review_policy_mismatch"
    assert len(provider.requests) == 1


async def test_conditional_review_skips_reviewer_after_successful_verification(
    client: httpx.AsyncClient,
) -> None:
    """Verify on-failure review proceeds directly to synthesis when checks pass."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(
            idempotency_key="agent-conditional-review-0001",
            review_policy="on_verification_failure",
        ),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "completed"
    assert not any(node["node_key"] == "independent_review" for node in result["nodes"])
    verification_node = next(
        node for node in result["nodes"] if node["node_key"] == "deterministic_verification"
    )
    assert verification_node["output"]["requires_independent_review"] is False
    assert verification_node["transition"]["selected_transition"] == "synthesize"
    assert len(provider.requests) == 3


async def test_nonblocking_reviewer_revision_proceeds_to_final_synthesis(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify minor Reviewer revisions are applied by final synthesis instead of discarded."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(reviewer_verdict="needs_revision")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-review-reject-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "completed"
    assert result["error"] is None
    assert result["final_output"] is not None
    review_node = next(node for node in result["nodes"] if node["node_key"] == "independent_review")
    assert review_node["output"]["verdict"] == "needs_revision"
    assert review_node["output"]["requires_replan"] is False
    assert review_node["output"]["requires_retry"] is False
    assert review_node["transition"]["selected_transition"] == "synthesize"
    assert review_node["transition"]["next_node_keys"] == ["final_synthesis"]
    assert review_node["output"]["evaluation_ids"]
    final_request = next(
        request
        for request in provider.requests
        if request.metadata["agent_node_key"] == "final_synthesis"
    )
    assert final_request.system_instruction is not None
    assert "Apply every non-blocking Reviewer required_action" in final_request.system_instruction
    final_model_input = json.loads(final_request.messages[-1].content)
    assert final_model_input["review"]["verdict"] == "needs_revision"
    evaluation_id = review_node["output"]["evaluation_ids"][0]
    async with test_database_session_factory() as db_session:
        evaluation = await db_session.get(LlmTaskEvaluation, evaluation_id)
    assert evaluation is not None
    assert evaluation.verdict == EvaluationVerdict.PASS


async def test_reviewer_fail_stops_before_final_synthesis(
    client: httpx.AsyncClient,
) -> None:
    """Verify an explicit Reviewer failure cannot become a successful final answer."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(reviewer_verdict="fail")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-review-fail-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "agent_review_rejected"
    assert result["final_output"] is None
    assert not any(node["node_key"] == "final_synthesis" for node in result["nodes"])


async def test_reviewer_revision_with_error_finding_stops_before_final_synthesis(
    client: httpx.AsyncClient,
) -> None:
    """Verify an error-level revision remains blocking even without a retry request."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(
        reviewer_verdict="needs_revision",
        reviewer_error_finding=True,
    )
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-review-error-revision-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "agent_review_rejected"
    assert result["final_output"] is None
    review_node = next(node for node in result["nodes"] if node["node_key"] == "independent_review")
    assert review_node["transition"]["selected_transition"] == "reject"
    assert not any(node["node_key"] == "final_synthesis" for node in result["nodes"])


async def test_reviewer_retry_request_uses_reject_transition(
    client: httpx.AsyncClient,
) -> None:
    """Verify a nominal pass that still requests retry cannot advertise synthesis."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(reviewer_requires_retry=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-review-retry-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "agent_review_rejected"
    review_node = next(node for node in result["nodes"] if node["node_key"] == "independent_review")
    assert review_node["output"]["verdict"] == "pass"
    assert review_node["output"]["requires_retry"] is True
    assert review_node["transition"]["selected_transition"] == "reject"
    assert review_node["transition"]["next_node_keys"] == []
    assert not any(node["node_key"] == "final_synthesis" for node in result["nodes"])


async def test_reviewer_pass_with_error_finding_is_rejected_as_invalid_output(
    client: httpx.AsyncClient,
) -> None:
    """Verify a blocking Reviewer finding cannot coexist with a passing verdict."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(reviewer_error_finding=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-review-error-finding-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "node_output_invalid"
    review_node = next(node for node in result["nodes"] if node["node_key"] == "independent_review")
    assert review_node["status"] == "failed"
    assert review_node["evidence"]["model_attempt_ids"]
    assert not any(node["node_key"] == "final_synthesis" for node in result["nodes"])


async def test_incomplete_finish_reason_fails_with_attempt_evidence(
    client: httpx.AsyncClient,
) -> None:
    """Verify schema-valid but truncated provider output is not accepted as complete."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(finish_reason="length")
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-length-finish-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    controller_node = next(
        node for node in result["nodes"] if node["node_key"] == "controller_planning"
    )
    assert result["status"] == "failed"
    assert controller_node["status"] == "failed"
    assert controller_node["evidence"]["model_attempt_ids"]
    assert controller_node["usage"]["model_call_count"] == 1


async def test_final_message_rolls_back_when_final_node_cannot_be_persisted(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify a failed final node cannot leave a misleading committed Assistant message."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(oversized_final_output=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-final-message-rollback-0001"),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "failed"
    assert result["final_output"] is None
    final_node = next(node for node in result["nodes"] if node["node_key"] == "final_synthesis")
    assert final_node["status"] == "failed"
    async with test_database_session_factory() as db_session:
        assistant_messages = list(
            (
                await db_session.scalars(
                    select(LlmMessage).where(
                        LlmMessage.run_id == run["run_id"],
                        LlmMessage.role == MessageRole.ASSISTANT,
                    )
                )
            ).all()
        )
    assert assistant_messages == []


async def test_worker_timeout_marks_unknown_outcome_and_finalizes_agent_state(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify the platform deadline stops a slow provider and removes false running facts."""

    run = await create_test_run(client)
    provider = SlowWorkerProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    payload = workflow_payload(idempotency_key="agent-worker-timeout-0001")
    payload["role_bindings"]["researcher"]["timeout_seconds"] = 1

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=payload,
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "outcome_unknown"
    assert result["error"]["error_code"] == "timeout"
    assert result["error"]["is_retryable"] is False
    worker_node = next(
        node for node in result["nodes"] if node["node_key"].startswith("worker_execution")
    )
    assert worker_node["status"] == "outcome_unknown"
    assert worker_node["public_view"]["status_label"] == "Node outcome unknown"
    async with test_database_session_factory() as db_session:
        attempts = list(
            (
                await db_session.scalars(
                    select(LlmModelAttempt).where(LlmModelAttempt.run_id == run["run_id"])
                )
            ).all()
        )
        agent_runs = list(
            (
                await db_session.scalars(
                    select(LlmAgentRun).where(LlmAgentRun.run_id == run["run_id"])
                )
            ).all()
        )
        turns = list((await db_session.scalars(select(LlmAgentTurn))).all())

    assert any(item.status == AttemptStatus.TIMED_OUT for item in attempts)
    assert all(item.status == AgentRunStatus.FAILED for item in agent_runs)
    assert all(item.status == AgentTurnStatus.FAILED for item in turns)


async def test_in_flight_provider_cancellation_marks_attempt_and_workflow_outcome_unknown(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify cancellation inside the provider boundary never claims a known outcome."""

    run = await create_test_run(client)
    provider = BlockingWorkerProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    request_task = asyncio.create_task(
        client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(idempotency_key="agent-provider-cancelled-0001"),
        )
    )
    await asyncio.wait_for(provider.worker_request_started.wait(), timeout=2)
    request_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request_task

    async with test_database_session_factory() as db_session:
        workflow = await db_session.scalar(
            select(LlmAgentWorkflowExecution).where(
                LlmAgentWorkflowExecution.run_id == run["run_id"]
            )
        )
        attempts = list(
            (
                await db_session.scalars(
                    select(LlmModelAttempt).where(LlmModelAttempt.run_id == run["run_id"])
                )
            ).all()
        )
        nodes = list(
            (
                await db_session.scalars(
                    select(LlmAgentWorkflowNodeExecution).where(
                        LlmAgentWorkflowNodeExecution.run_id == run["run_id"]
                    )
                )
            ).all()
        )

    assert workflow is not None
    assert workflow.status == AgentWorkflowStatus.OUTCOME_UNKNOWN
    assert workflow.error_json["is_retryable"] is False
    worker_attempt = next(item for item in attempts if item.task_id is not None)
    assert worker_attempt.status == AttemptStatus.OUTCOME_UNKNOWN
    worker_node = next(item for item in nodes if item.node_key.startswith("worker_execution"))
    assert worker_node.status == AgentWorkflowNodeStatus.OUTCOME_UNKNOWN


async def test_invalid_last_event_id_is_rejected(client: httpx.AsyncClient) -> None:
    """Verify malformed SSE recovery state cannot silently trigger a full replay."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    created = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-invalid-event-id-0001"),
    )

    response = await client.get(
        f"/api/v1/agent-workflows/{created.json()['workflow_execution_id']}/events",
        headers={"Last-Event-ID": "not-a-sequence"},
    )

    assert response.status_code == 422


async def test_workflow_openapi_declares_json_and_sse_response_contracts(
    client: httpx.AsyncClient,
) -> None:
    """Verify generated API docs expose create/replay media and replay status codes."""

    openapi = (await client.get("/openapi.json")).json()
    create_operation = openapi["paths"]["/api/v1/runs/{run_id}/agent-workflows"]["post"]
    for status_code in ["200", "201"]:
        content = create_operation["responses"][status_code]["content"]
        assert "application/json" in content
        assert "text/event-stream" in content
    event_operation = openapi["paths"]["/api/v1/agent-workflows/{workflow_execution_id}/events"][
        "get"
    ]
    event_content = event_operation["responses"]["200"]["content"]
    assert "text/event-stream" in event_content
    assert "application/json" not in event_content
    output_schema = openapi["components"]["schemas"]["AgentWorkflowNodeResultRead"]["properties"][
        "output"
    ]
    output_references = {item.get("$ref") for item in output_schema["anyOf"] if "$ref" in item}
    assert "#/components/schemas/RequestIntakeOutput" in output_references
    assert "#/components/schemas/WorkflowCompletionOutput" in output_references
    dispatch_properties = openapi["components"]["schemas"]["AgentDispatchOutput"]["properties"]
    assert dispatch_properties["created_tasks"]["items"]["$ref"] == (
        "#/components/schemas/DispatchedAgentTask"
    )
    assert dispatch_properties["created_agent_runs"]["items"]["$ref"] == (
        "#/components/schemas/DispatchedAgentRun"
    )
    cancel_operation = openapi["paths"]["/api/v1/agent-workflows/{workflow_execution_id}/cancel"][
        "post"
    ]
    assert cancel_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AgentWorkflowSummaryRead"
    }
    workflow_create_properties = openapi["components"]["schemas"]["AgentWorkflowCreate"][
        "properties"
    ]
    assert workflow_create_properties["max_parallel_agents"]["maximum"] == 2


async def test_verbose_worker_output_is_compacted_into_a_valid_handoff(
    client: httpx.AsyncClient,
) -> None:
    """Keep the complete Worker result while bounding its collaboration Handoff."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider(verbose_worker_output=True)
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-verbose-handoff-0001"),
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "completed", result
    worker_node = next(
        node for node in result["nodes"] if node["output_type"] == "agent_model_execution"
    )
    handoff_node = next(
        node for node in result["nodes"] if node["output_type"] == "agent_handoff"
    )
    assert len(worker_node["output"]["structured_output"]["confirmed_facts"][0]) > 1_000
    serialized_handoff = json.dumps(
        {
            key: value
            for key, value in handoff_node["output"].items()
            if key
            not in {
                "agent_handoff_id",
                "schema_version",
                "supersedes_handoff_id",
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert len(serialized_handoff.encode()) <= HANDOFF_V1_HARD_CAP_TOKENS
    assert handoff_node["output"]["invariants_for_next_agent"] == [
        f"Complete Worker output is preserved in node {worker_node['node_execution_id']}."
    ]


async def test_node_result_rejects_unregistered_output_schema_version(
    client: httpx.AsyncClient,
) -> None:
    """Verify node type and output schema version form one validated public contract."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    result = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(idempotency_key="agent-output-version-0001"),
        )
    ).json()
    intake_node = dict(result["nodes"][0])
    intake_node["output_schema_version"] = "request_intake_output.v999"

    with pytest.raises(ValueError, match="output_schema_version"):
        AgentWorkflowNodeResultRead.model_validate(intake_node)


async def test_handoff_rejects_task_different_from_agent_run(
    client: httpx.AsyncClient,
) -> None:
    """Verify one Agent cannot attach its Handoff to another task in the same Run."""

    run = await create_test_run(client)
    first_task = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/tasks",
            json={"task_type": "analysis", "title": "A", "objective": "Analyze A"},
        )
    ).json()
    second_task = (
        await client.post(
            f"/api/v1/runs/{run['run_id']}/tasks",
            json={"task_type": "analysis", "title": "B", "objective": "Analyze B"},
        )
    ).json()
    agent_run = await client.post(
        "/api/v1/internal/agent-runs",
        json={
            "run_id": run["run_id"],
            "task_id": first_task["task_id"],
            "agent_role": "researcher",
        },
        headers=INTERNAL_HEADERS,
    )

    response = await client.post(
        f"/api/v1/internal/runs/{run['run_id']}/agent-handoffs",
        json={
            "agent_run_id": agent_run.json()["agent_run_id"],
            "task_id": second_task["task_id"],
            "schema_version": "agent_handoff.v1",
            "status": "completed",
            "handoff_json": {
                "objective": "Analyze A",
                "status": "completed",
                "confirmed_facts": [],
                "decisions": [],
                "files_read": [],
                "files_changed": [],
                "artifacts": [],
                "tests": [],
                "remaining_work": [],
                "risks": [],
                "unknowns": [],
                "invariants_for_next_agent": [],
            },
            "idempotency_key": "wrong-task-handoff-0001",
        },
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 409


async def test_independent_workers_run_concurrently_with_separate_execution_sessions(
    client: httpx.AsyncClient,
) -> None:
    """Verify two dependency-free Worker calls overlap when concurrency is set to two."""

    run = await create_test_run(client)
    provider = ConcurrentWorkerProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(
            idempotency_key="agent-parallel-workers-0001",
            max_parallel_agents=2,
        ),
    )

    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "completed", result
    assert sorted(provider.started_worker_node_keys) == [
        "worker_execution.plan-1",
        "worker_execution.research-1",
    ]
    dispatch_node = next(node for node in result["nodes"] if node["node_key"] == "agent_dispatch")
    assert dispatch_node["output"]["dispatch_groups"][0]["concurrency_limit"] == 2
    assert len(dispatch_node["output"]["dispatch_groups"][0]["agent_run_ids"]) == 2


async def test_model_cost_reservations_prevent_concurrent_budget_oversubscription(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify only one of two reservations can consume the same remaining Run budget."""

    run = await create_test_run(client)
    async with test_database_session_factory() as setup_session:
        service = AgentWorkflowExecutionService(
            registry=scripted_registry(ScriptedAgentProvider()),
            prices=app.dependency_overrides[get_price_catalog](),
            db_session=setup_session,
            db_session_factory=test_database_session_factory,
            storage=app.dependency_overrides[get_object_storage](),
        )
        workflow, replayed = await service.prepare_workflow(
            run["run_id"],
            AgentWorkflowCreate.model_validate(
                workflow_payload(idempotency_key="agent-concurrent-cost-reservation-0001")
            ),
        )
        workflow_execution_id = workflow.workflow_execution_id
    assert replayed is False

    async def reserve_three_dollars() -> Exception | None:
        """Attempt one independent durable reservation and expose expected rejection."""

        async with test_database_session_factory() as reservation_session:
            durable_workflow = await reservation_session.get(
                LlmAgentWorkflowExecution,
                workflow_execution_id,
            )
            assert durable_workflow is not None
            state_service = WorkflowExecutionStateService(
                db_session=reservation_session,
                workflow_definitions=DEFAULT_WORKFLOW_DEFINITION_REGISTRY,
            )
            try:
                await state_service.reserve_model_call(
                    durable_workflow,
                    reserved_estimated_cost=Decimal("3.000000"),
                )
            except InvalidRequestError as error:
                return error
            return None

    reservation_results = await asyncio.gather(
        reserve_three_dollars(),
        reserve_three_dollars(),
    )
    assert sum(result is None for result in reservation_results) == 1
    rejected_reservation = next(result for result in reservation_results if result is not None)
    assert isinstance(rejected_reservation, InvalidRequestError)
    assert "budget" in str(rejected_reservation).lower()

    async with test_database_session_factory() as verification_session:
        durable_workflow = await verification_session.get(
            LlmAgentWorkflowExecution,
            workflow_execution_id,
        )
        durable_run = await verification_session.get(LlmRun, run["run_id"])
        assert durable_workflow is not None
        assert durable_run is not None
        assert durable_workflow.model_call_count == 1
        assert durable_workflow.reserved_estimated_cost == Decimal("3.000000")
        assert durable_run.cost_used == Decimal("0.000000")
        state_service = WorkflowExecutionStateService(
            db_session=verification_session,
            workflow_definitions=DEFAULT_WORKFLOW_DEFINITION_REGISTRY,
        )
        await state_service.release_model_cost_reservation(
            durable_workflow,
            reserved_estimated_cost=Decimal("3.000000"),
        )
        await verification_session.refresh(durable_workflow)
        assert durable_workflow.reserved_estimated_cost == Decimal("0.000000")


async def test_budgeted_workflow_rejects_unknown_model_price_before_provider(
    client: httpx.AsyncClient,
) -> None:
    """Verify a Run budget is enforceable only when its selected model has explicit prices."""

    run = await create_test_run(client)
    provider = ScriptedAgentProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    app.dependency_overrides[get_price_catalog] = lambda: PriceCatalog()

    response = await client.post(
        f"/api/v1/runs/{run['run_id']}/agent-workflows",
        json=workflow_payload(idempotency_key="agent-unknown-price-budget-0001"),
    )

    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "failed"
    assert result["error"]["error_code"] == "agent_budget_price_unavailable"
    assert provider.requests == []


async def test_external_cancel_stops_workflow_before_another_node_starts(
    client: httpx.AsyncClient,
) -> None:
    """Verify the cancel action is durable while an already-dispatched Provider call finishes."""

    run = await create_test_run(client)
    provider = ReleasableWorkerProvider()
    app.dependency_overrides[get_provider_registry] = lambda: scripted_registry(provider)
    execution_request = asyncio.create_task(
        client.post(
            f"/api/v1/runs/{run['run_id']}/agent-workflows",
            json=workflow_payload(idempotency_key="agent-external-cancel-0001"),
        )
    )
    await asyncio.wait_for(provider.worker_request_started.wait(), timeout=2)
    workflow_summary = (await client.get(f"/api/v1/runs/{run['run_id']}/agent-workflow")).json()

    cancellation_response = await client.post(
        f"/api/v1/agent-workflows/{workflow_summary['workflow_execution_id']}/cancel"
    )
    provider.release_worker_request.set()
    execution_response = await asyncio.wait_for(execution_request, timeout=2)
    repeated_cancellation_response = await client.post(
        f"/api/v1/agent-workflows/{workflow_summary['workflow_execution_id']}/cancel"
    )

    assert cancellation_response.status_code == 200
    assert cancellation_response.json()["status"] == "cancelled"
    assert execution_response.status_code == 201
    assert execution_response.json()["status"] == "cancelled"
    assert repeated_cancellation_response.status_code == 200
    assert repeated_cancellation_response.json()["status"] == "cancelled"
    assert not any(
        request.metadata["agent_node_key"] in {"independent_review", "final_synthesis"}
        for request in provider.requests
    )


async def test_completion_node_and_workflow_terminal_events_share_one_commit(
    client: httpx.AsyncClient,
    test_database_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify observers never see a completed aggregation node on a running workflow."""

    run = await create_test_run(client)
    observed_atomic_completion = False

    async def verify_terminal_commit(event) -> None:
        """Check both terminal facts when the completion-node event becomes visible."""

        nonlocal observed_atomic_completion
        if not (
            event.event_type == "agent.node.completed"
            and event.public_payload.get("node_key") == "workflow_completion"
        ):
            return
        async with test_database_session_factory() as observer_session:
            workflow = await observer_session.get(
                LlmAgentWorkflowExecution,
                event.workflow_execution_id,
            )
            terminal_event = await observer_session.scalar(
                select(LlmAgentWorkflowEvent).where(
                    LlmAgentWorkflowEvent.workflow_execution_id == event.workflow_execution_id,
                    LlmAgentWorkflowEvent.event_type == "agent.workflow.completed",
                )
            )
        assert workflow is not None
        assert workflow.status == AgentWorkflowStatus.COMPLETED
        assert terminal_event is not None
        observed_atomic_completion = True

    async with test_database_session_factory() as execution_session:
        service = AgentWorkflowExecutionService(
            registry=scripted_registry(ScriptedAgentProvider()),
            prices=app.dependency_overrides[get_price_catalog](),
            db_session=execution_session,
            db_session_factory=test_database_session_factory,
            storage=app.dependency_overrides[get_object_storage](),
        )
        result, replayed = await service.create_and_execute(
            run["run_id"],
            AgentWorkflowCreate.model_validate(
                workflow_payload(idempotency_key="agent-atomic-completion-0001")
            ),
            event_sink=verify_terminal_commit,
        )

    assert replayed is False
    assert result.status == AgentWorkflowStatus.COMPLETED
    assert observed_atomic_completion is True
