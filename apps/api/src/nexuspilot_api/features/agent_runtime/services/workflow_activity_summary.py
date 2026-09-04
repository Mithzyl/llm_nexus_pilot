"""Build bounded public Agent node activity from durable workflow facts.

This module deliberately describes lifecycle work instead of model reasoning. It
only reads allow-listed input and typed-output fields and never receives prompts,
provider payloads, credentials, or internal exception strings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from nexuspilot_api.features.agent_runtime.schemas.agent_workflows import (
    NodeActivitySummaryRead,
)
from nexuspilot_api.models import AgentWorkflowNodeStatus

MAX_NODE_ACTIVITY_TEXT_CHARACTERS = 240
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _bounded_plain_text(value: object, *, fallback: str) -> str:
    """Normalize one public value to bounded plain text without rendering markup."""

    normalized = " ".join(_CONTROL_CHARACTERS.sub("", str(value or "")).split())
    selected = normalized or fallback
    if len(selected) <= MAX_NODE_ACTIVITY_TEXT_CHARACTERS:
        return selected
    return selected[: MAX_NODE_ACTIVITY_TEXT_CHARACTERS - 1].rstrip() + "…"


def _activity(
    *,
    phase: str,
    text: str,
    source: str,
) -> NodeActivitySummaryRead:
    """Validate one generated activity through the public versioned contract."""

    return NodeActivitySummaryRead(
        phase=phase,
        text=_bounded_plain_text(text, fallback="工作流节点状态已更新"),
        source=source,
    )


def _list_count(output: Mapping[str, Any], field_name: str) -> int:
    """Count one list-like typed-output field without accepting arbitrary objects."""

    value = output.get(field_name)
    return len(value) if isinstance(value, list) else 0


def _public_agent_label(agent_role: str | None) -> str:
    """Present an optional internal role as a readable Agent label."""

    if not agent_role:
        return "Agent"
    role = _bounded_plain_text(agent_role, fallback="Agent")
    return f"Agent（{role}）"


def build_running_node_activity(
    *,
    output_type: str,
    input_values: Mapping[str, Any],
    agent_role: str | None,
) -> NodeActivitySummaryRead:
    """Describe work that has durably started using only safe pre-call input facts."""

    task_title = _bounded_plain_text(input_values.get("task_title"), fallback="当前任务")
    agent_label = _public_agent_label(agent_role)
    task_count = _list_count(input_values, "tasks")
    texts = {
        "request_intake": "正在确认请求目标与能力边界",
        "context_assembly": "正在准备本次运行所需上下文",
        "controller_plan": "Controller 正在规划执行步骤",
        "plan_validation": "正在检查计划依赖与能力边界",
        "agent_dispatch": (f"正在分配{task_count}项工作任务" if task_count else "正在分配工作任务"),
        "agent_model_execution": f"{agent_label}正在执行：“{task_title}”",
        "agent_handoff": f"正在整理“{task_title}”的交接结果",
        "deterministic_verification": "正在核对交接结构、引用与完成状态",
        "independent_review": "正在独立审核候选结果与证据",
        "final_synthesis": "正在规划最终答案",
        "workflow_completion": "正在提交工作流完成事实",
    }
    return _activity(
        phase="running",
        text=texts.get(output_type, f"正在执行{agent_role or '工作流'}节点"),
        source="deterministic_input",
    )


def build_completed_node_activity(
    *,
    output_type: str,
    output: Mapping[str, Any],
    activity_context: Mapping[str, Any],
    agent_role: str | None,
) -> NodeActivitySummaryRead:
    """Describe a completed node after its typed output has passed validation."""

    text: str
    if output_type == "request_intake":
        text = "已确认请求目标与能力边界"
    elif output_type == "context_assembly":
        included_count = sum(
            _list_count(output, field_name)
            for field_name in (
                "included_message_ids",
                "included_artifact_ids",
                "included_handoff_ids",
            )
        )
        excluded_count = _list_count(output, "excluded_sources")
        text = f"已准备上下文：纳入{included_count}项，排除{excluded_count}项"
    elif output_type == "controller_plan":
        decision_summary = _bounded_plain_text(
            output.get("decision_summary"),
            fallback=f"已制定{_list_count(output, 'tasks')}项执行计划",
        )
        text = f"Controller 已规划：“{decision_summary}”"
    elif output_type == "plan_validation":
        if output.get("is_valid") is True:
            text = f"计划检查通过，{int(output.get('total_task_count') or 0)}项任务可执行"
        else:
            failed_checks = sum(
                1
                for check in output.get("checks", [])
                if isinstance(check, Mapping) and check.get("status") == "fail"
            )
            text = f"计划不可执行，发现{failed_checks}项问题"
    elif output_type == "agent_dispatch":
        text = (
            f"已分配{_list_count(output, 'created_tasks')}项任务，"
            f"形成{_list_count(output, 'dispatch_groups')}个执行组"
        )
    elif output_type == "agent_model_execution":
        structured_output = output.get("structured_output")
        worker_summary = (
            structured_output.get("summary") if isinstance(structured_output, Mapping) else None
        )
        task_title = _bounded_plain_text(activity_context.get("task_title"), fallback="当前任务")
        summary_text = _bounded_plain_text(worker_summary, fallback="已提交类型化结果")
        agent_label = _public_agent_label(agent_role)
        text = f"{agent_label}已提交“{task_title}”的阶段结果：{summary_text}"
    elif output_type == "agent_handoff":
        objective = _bounded_plain_text(output.get("objective"), fallback="当前任务结果")
        agent_label = _public_agent_label(agent_role)
        text = f"已接收 {agent_label}的阶段结果：“{objective}”"
    elif output_type == "deterministic_verification":
        if output.get("verdict") == "pass":
            text = f"校验通过，已确认{_list_count(output, 'verified_claims')}项"
        else:
            text = f"校验发现{_list_count(output, 'blocking_findings')}项阻塞问题"
    elif output_type == "independent_review":
        verdict = output.get("verdict")
        findings = output.get("findings", [])
        finding_count = len(findings) if isinstance(findings, list) else 0
        error_count = sum(
            1
            for finding in findings
            if isinstance(finding, Mapping) and finding.get("severity") == "error"
        )
        if verdict == "pass":
            text = "审核通过"
        elif verdict == "needs_revision":
            text = f"审核建议修订，发现{finding_count}项问题"
        else:
            text = f"审核未通过，发现{error_count}项错误"
    elif output_type == "final_synthesis":
        text = (
            f"最终答案已生成：覆盖{_list_count(output, 'completed_objectives')}项目标，"
            f"保留{_list_count(output, 'unresolved_items')}项未解决事项"
        )
    elif output_type == "workflow_completion":
        text = (
            f"最终答案已提交：{int(output.get('node_count') or 0)}个节点，"
            f"{_list_count(output, 'model_attempt_ids')}次模型调用"
        )
    else:
        text = f"已完成{agent_role or '工作流'}节点"
    return _activity(phase="completed", text=text, source="typed_output")


def build_terminal_node_activity(
    *,
    node_status: AgentWorkflowNodeStatus,
    public_error_message: str,
) -> NodeActivitySummaryRead:
    """Map a durable terminal node state to one safe recovery-oriented activity."""

    if node_status == AgentWorkflowNodeStatus.CANCELLED:
        return _activity(
            phase="cancelled",
            text="执行已取消",
            source="error_contract",
        )
    if node_status == AgentWorkflowNodeStatus.OUTCOME_UNKNOWN:
        return _activity(
            phase="outcome_unknown",
            text="模型调用结果暂无法确认",
            source="error_contract",
        )
    if node_status != AgentWorkflowNodeStatus.FAILED:
        raise ValueError(f"Node status {node_status.value} is not a supported terminal failure")
    safe_message = _bounded_plain_text(public_error_message, fallback="节点执行失败")
    return _activity(
        phase="failed",
        text=f"执行失败：{safe_message}",
        source="error_contract",
    )
