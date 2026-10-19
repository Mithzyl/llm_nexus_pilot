import type {
  AgentWorkflowEvent,
  AgentWorkflowNodeActivitySummary,
  AgentWorkflowNodeOutputMap,
  AgentWorkflowNodeResult,
  AgentWorkflowNodeStatus,
  AgentWorkflowStatus,
} from "./types";
import { isAgentWorkflowNodeActivitySummary } from "./agent-workflow";

type AgentDispatchGroup = AgentWorkflowNodeOutputMap["agent_dispatch"]["dispatch_groups"][number];
type AgentNodeBudget = AgentWorkflowNodeResult["budget"];

const AGENT_TOOL_LABELS: Record<string, string> = {
  search_tools: "查找可用工具",
  describe_tools: "查看工具说明",
  list_run_files: "列出附件",
  get_file_outline: "查看文件结构",
  search_file_content: "搜索文件文字",
  read_file_content: "读取文件文字",
  read_file_table: "读取表格",
  delegate_task: "委派子任务",
  save_task_note: "保存任务笔记",
  read_task_note: "读取任务笔记",
  list_task_notes: "列出任务笔记",
  get_task_read_coverage: "查看阅读进度",
};

const AGENT_TOOL_STATUS_LABELS: Record<string, string> = {
  completed: "读取结果已保存",
  permission_denied: "未获授权或文件不可用",
  failed: "读取失败",
  timed_out: "读取超时",
  cancelled: "已取消",
  requested: "等待执行",
  running: "正在读取",
  outcome_unknown: "结果待确认",
};

/** Keep tool-specific wording out of the generic file-read status vocabulary. */
const AGENT_TOOL_STATUS_OVERRIDES: Record<string, Record<string, string>> = {
  delegate_task: {
    completed: "委派完成", running: "委派中", failed: "委派失败",
    permission_denied: "未获准委派", timed_out: "委派超时",
  },
  save_task_note: {
    completed: "已保存", running: "正在保存", failed: "保存失败",
    requested: "等待保存", timed_out: "保存超时",
  },
  read_task_note: { completed: "已读取" },
  list_task_notes: {
    completed: "已列出", running: "正在查询", failed: "查询失败",
    requested: "等待查询", timed_out: "查询超时",
  },
  get_task_read_coverage: {
    completed: "已返回范围", running: "正在查询", failed: "查询失败",
    requested: "等待查询", timed_out: "查询超时",
  },
};

/** Name registered tools without presenting an unknown name as a permission failure. */
export function agentToolLabel(toolName: string): string {
  return AGENT_TOOL_LABELS[toolName] ?? "未知工具";
}

/** Use the tool's actual effect rather than calling every completion a file read. */
export function agentToolStatusLabel(toolName: string, status: string): string {
  if (!(toolName in AGENT_TOOL_LABELS)) return "状态待确认";
  return (
    AGENT_TOOL_STATUS_OVERRIDES[toolName]?.[status] ??
    AGENT_TOOL_STATUS_LABELS[status] ??
    "状态待确认"
  );
}

/** Name the delegated role without exposing internal task or Agent identifiers. */
export function agentDelegationRoleLabel(role: string | null, nodeKey: string): string | null {
  if (role === "planner") return "Planner";
  if (role === "researcher") return "Researcher";
  if (nodeKey.startsWith("general_delegate.")) return "委派";
  return null;
}

/** Summarize returned blocks without claiming the model understood the whole file. */
export function agentCandidateCoverageSummary(
  output: AgentWorkflowNodeOutputMap["general_candidate"],
): string | null {
  const coverage = output.read_coverage ?? [];
  if (coverage.length === 0) return null;
  const returned = coverage.reduce((total, item) => total + item.returned_block_ids.length, 0);
  const parsed = coverage.reduce((total, item) => total + item.total_parsed_blocks, 0);
  if (coverage.every((item) => item.all_parsed_blocks_returned)) {
    return `${coverage.length} 个文件 · 全部返回`;
  }
  return `${coverage.length} 个文件 · ${returned} / ${parsed} 块`;
}

/** Only a persisted cancellation may be presented as a confirmed terminal state. */
export function cancelledMessageStatusLabel(confirmed?: boolean): string {
  return confirmed ? "已取消" : "已停止 · 等待服务端最终状态";
}

export type AgentWorkflowActivityItem = AgentWorkflowNodeActivitySummary & {
  nodeExecutionId: string;
  eventSequence: number;
  occurredAt: string;
};

/** Identify the persisted assistant message that represents one Agent Workflow Run. */
export function isAgentWorkflowAssistantMessage(
  message: { role: string; run_id: string | null },
  workflowRunId: string,
): boolean {
  return message.role === "assistant" && message.run_id === workflowRunId;
}

/** Prefer the versioned activity while retaining readable historical node snapshots. */
export function agentNodePublicActivityText(
  publicView: AgentWorkflowNodeResult["public_view"],
): string {
  return isAgentWorkflowNodeActivitySummary(publicView.activity)
    ? publicView.activity.text
    : publicView.summary;
}

/** Read one validated activity from a persisted node event when the version is supported. */
export function agentWorkflowEventActivity(
  event: AgentWorkflowEvent,
): AgentWorkflowActivityItem | null {
  if (!event.node_execution_id) return null;
  const activity = event.public_payload.activity;
  if (isAgentWorkflowNodeActivitySummary(activity)) {
    return {
      ...activity,
      nodeExecutionId: event.node_execution_id,
      eventSequence: event.event_sequence,
      occurredAt: event.occurred_at,
    };
  }
  if (event.event_type === "agent.delegation.revision.requested") {
    return {
      schema_version: "node_activity_summary.v1",
      phase: "running",
      text: event.public_summary || "候选回答需要修订，继续由原 Agent 处理",
      source: "typed_output",
      nodeExecutionId: event.node_execution_id,
      eventSequence: event.event_sequence,
      occurredAt: event.occurred_at,
    };
  }
  return null;
}

/** Preserve every public lifecycle transition in durable event-sequence order. */
export function selectAgentWorkflowActivityTimeline(
  events: AgentWorkflowEvent[],
): AgentWorkflowActivityItem[] {
  const activityByEventSequence = new Map<number, AgentWorkflowActivityItem>();
  for (const event of events) {
    const activity = agentWorkflowEventActivity(event);
    if (activity) activityByEventSequence.set(activity.eventSequence, activity);
  }
  return [...activityByEventSequence.values()].sort(
    (left, right) => left.eventSequence - right.eventSequence,
  );
}

/** Show only the newest activity when collapsed and the complete timeline when expanded. */
export function selectVisibleAgentWorkflowActivities(
  activities: AgentWorkflowActivityItem[],
  isExpanded: boolean,
): AgentWorkflowActivityItem[] {
  return isExpanded ? activities : activities.slice(-1);
}

/** Describe persisted dispatch groups without inferring dependencies from event arrival order. */
export function agentDispatchGroupSummary(dispatchGroups: AgentDispatchGroup[]): string {
  if (dispatchGroups.length === 0) return "无执行组";
  return dispatchGroups
    .map(
      (group) =>
        `${group.group_id} · ${group.agent_run_ids.length} 个 Agent · 并发 ${group.concurrency_limit}`,
    )
    .join("；");
}

/** Keep consumed cost and the temporary concurrent-call reservation visibly separate. */
export function agentNodeBudgetSummary(
  budget: Pick<AgentNodeBudget, "cost_used_after" | "reserved_estimated_cost">,
): Array<[string, string]> {
  return [
    ["已消费费用", String(budget.cost_used_after)],
    ["当前费用预留", String(budget.reserved_estimated_cost)],
  ];
}

/** Explain absent output from the persisted node lifecycle instead of implying one cause. */
export function agentNodeMissingOutputLabel(status: AgentWorkflowNodeStatus): string {
  const labels: Record<AgentWorkflowNodeStatus, string> = {
    pending: "节点等待执行，尚未提交输出",
    running: "节点正在运行，尚未提交输出",
    completed: "节点已完成，但提交的输出事实缺失",
    skipped: "节点已跳过，不会生成输出",
    blocked: "节点被阻塞，未生成可提交输出",
    failed: "节点执行失败，未生成可提交输出",
    cancelled: "节点已取消，未生成可提交输出",
    outcome_unknown: "节点结果未知，无法确认是否生成输出",
  };
  return labels[status];
}

/** Expose the cancel action only while the public Workflow status is running. */
export function isAgentWorkflowCancellable(status: AgentWorkflowStatus): boolean {
  return status === "running";
}
