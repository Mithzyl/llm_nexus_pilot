"""Run a configured child role and preserve its actual prose and originals in a Handoff."""

import copy
import hashlib

from nexuspilot_models.contracts import Message, MessageRole
from sqlalchemy import func, select

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import GeneralCandidateOutput
from nexuspilot_api.features.agent_runtime.services.agent_tool_loop import ToolLoopState
from nexuspilot_api.features.agent_runtime.services.general_candidate_service import (
    build_candidate_units,
    build_read_coverage,
)
from nexuspilot_api.features.memory.schemas.collaboration_memory import AgentHandoffCreate
from nexuspilot_api.features.memory.services.collaboration_memory_service import submit_handoff
from nexuspilot_api.models import AgentRunStatus, LlmAgentRun, LlmAgentWorkflowNodeExecution, new_id
from nexuspilot_api.services.agent_file_tool_service import ToolOperationError


async def execute_general_delegation(
    owner, workflow, parent_node, payload, parent_task, parent_state, arguments
):
    """Execute one nonrecursive child under parent time/cancel limits and fixed role bindings.

    The coordinator supplies identities and distinguishes the assignment from the parent goal.
    Child prose stays provisional; the main candidate follows the original review policy.
    Failures propagate through existing tool/workflow finalization and never become
    successful handoffs.
    """
    if arguments.role not in payload.role_bindings:
        raise ToolOperationError("delegated_role_not_configured", status="permission_denied")
    if set(arguments.source_aliases) - owner.sources.keys():
        raise ToolOperationError("unknown_delegated_source")
    await owner.workflow_state.ensure_workflow_running(workflow)
    node_count = await owner.db_session.scalar(select(func.count()).select_from(
        LlmAgentWorkflowNodeExecution,
    ).where(LlmAgentWorkflowNodeExecution.workflow_execution_id == workflow.workflow_execution_id))
    if node_count + 3 > workflow.max_nodes:
        raise ToolOperationError("delegation_cannot_fit_final_nodes")
    run = await owner._require_run(workflow.run_id)
    binding = payload.role_bindings[arguments.role]
    task = await owner._new_task(run, arguments.role, binding)
    task.title = "Delegated analysis"
    task.objective = arguments.objective
    task.parent_task_id = parent_task.task_id
    await owner.db_session.flush()
    agent = LlmAgentRun(
        agent_run_id=new_id(),
        workflow_execution_id=workflow.workflow_execution_id,
        run_id=workflow.run_id,
        task_id=task.task_id,
        agent_role=arguments.role,
        status=AgentRunStatus.PENDING,
        provider=binding.provider.value,
        model=binding.model,
    )
    owner.db_session.add(agent)
    await owner.db_session.flush()
    await owner._start_task_and_agent(task, agent)
    originals = await owner._resolve_sources(workflow)
    initial_aliases = set(arguments.source_aliases)
    prior_aliases = set(owner.sources)
    values = {
        "objective": task.objective,
        "run_objective": run.user_request,
        "execution_assignment": {
            "role": task.assigned_role,
            "task_id": task.task_id,
            "parent_task_id": parent_task.task_id,
            "delegation_already_started": True,
            "can_delegate": False,
        },
        "original_sources": {alias: originals[alias] for alias in initial_aliases},
    }
    node = await owner._start_node(
        workflow,
        node_key=f"general_delegate.{task.task_id}",
        node_type="model",
        output_type="general_delegated_candidate",
        input_values=values,
        task_id=task.task_id,
        agent_run_id=agent.agent_run_id,
        agent_role=arguments.role,
        status_label="Agent working",
        summary="Agent working",
    )
    turn = await owner._start_agent_turn(agent, node)
    state = ToolLoopState(deadline=parent_state.deadline)
    seen = set()
    while True:
        response = await owner._turn(
            workflow,
            node,
            payload,
            task,
            state,
            f"You are already the delegated {task.assigned_role} Agent identified in "
            "execution_assignment. The parent has already started this delegation. "
            "Complete only objective; run_objective and the original user conversation "
            "are background for understanding the parent's goal, not additional assigned steps. "
            "Keep the user's constraints, but do not take over the parent's steps. "
            "Do not repeat the parent's delegation or input-audit steps unless objective "
            "explicitly asks for an input audit. Recursive delegation is unavailable; "
            "do not search for a tool named after your role or report this delegation as missing. "
            "Return full useful prose, not JSON. "
            "Cite actual short sources as [S1]; report uncertainty and unread scope. "
            "This is provisional work for the main Agent, not approval or a final user answer.",
            values,
        )
        owner.model_node_service._require_complete_response(response)
        text = response.output_text or ""
        available = initial_aliases | (owner.sources.keys() - prior_aliases)
        units = build_candidate_units(text, available)
        cited = {alias for unit in units for alias in unit.source_aliases}
        issues = [f"Unknown source [{alias}]" for alias in sorted(cited - available)]
        if available and not cited:
            issues.append("Read evidence exists; cite actual source aliases using [S1] format")
        if not issues:
            break
        marker = hashlib.sha256((text + str(issues)).encode()).hexdigest()
        if marker in seen:
            raise InvalidRequestError(
                "Delegated Agent repeated invalid references without progress"
            )
        seen.add(marker)
        await owner.workflow_state.append_event(
            workflow, event_type="agent.delegation.revision.requested",
            public_summary="候选回答需要修订，继续由原 Agent 处理",
            public_payload={"task_id": task.task_id, "revision_index": len(seen),
                            "source_attempt_id": response.id}, node=node,
        )
        state.history.extend(
            [
                Message(role=MessageRole.ASSISTANT, content=text),
                Message(role=MessageRole.USER, content="\n".join(issues)),
            ]
        )
    originals = await owner._resolve_sources(workflow)
    candidate = GeneralCandidateOutput(
        schema_version="general_candidate.v2",
        text=text,
        units=units,
        sources={alias: copy.deepcopy(owner.sources[alias]) for alias in available},
        read_coverage=build_read_coverage({alias: originals[alias] for alias in available}),
    )
    await owner._finish_general_node(workflow, node, candidate, "general_agent", response.id)
    await owner._complete_agent_turn(turn, agent, response.id)
    await owner.workflow_state.ensure_workflow_running(workflow)
    result = await submit_handoff(
        owner.db_session,
        owner.storage,
        workflow.run_id,
        AgentHandoffCreate(
            agent_run_id=agent.agent_run_id,
            task_id=task.task_id,
            schema_version="agent_handoff.v3",
            handoff_json={
                "objective": task.objective,
                "status": "completed",
                "candidate_node_execution_id": node.node_execution_id,
                "candidate": candidate.model_dump(mode="json"),
            },
            idempotency_key=f"general-delegation:{node.node_execution_id}",
        ),
    )
    # Generating a candidate is not successful delivery. A failed Handoff write
    # must leave the task active for the outer failure/cancellation finalizer.
    await owner.workflow_state.ensure_workflow_running(workflow)
    await owner._complete_task_and_agent(task, agent)
    owner.delegated_agent_run_ids.append(agent.agent_run_id)
    owner.delegated_handoff_ids.append(result.record.agent_handoff_id)
    # Link actual completed children for downstream source authorization; no edge
    # back to the still-running parent is created, so ancestry remains acyclic.
    parent_node.input_json = {
        **parent_node.input_json,
        "source_node_execution_ids": [
            *parent_node.input_json.get("source_node_execution_ids", []),
            node.node_execution_id,
        ],
    }
    await owner.db_session.commit()
    return {
        "handoff_id": result.record.agent_handoff_id,
        "task_id": task.task_id,
        "agent_run_id": agent.agent_run_id,
        "candidate": candidate.model_dump(mode="json"),
        "original_sources": {alias: originals[alias] for alias in cited},
        "content_verified": False,
        "revision_count": len(seen),
    }
