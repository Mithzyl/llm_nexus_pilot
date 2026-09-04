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

export type AgentWorkflowActivityItem = AgentWorkflowNodeActivitySummary & {
  nodeExecutionId: string;
  eventSequence: number;
};

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
  const activity = event.public_payload.activity;
  if (!event.node_execution_id || !isAgentWorkflowNodeActivitySummary(activity)) return null;
  return {
    ...activity,
    nodeExecutionId: event.node_execution_id,
    eventSequence: event.event_sequence,
  };
}

/** Keep every parallel running node, then fill a compact feed with recent completions. */
export function selectRecentAgentWorkflowActivities(
  events: AgentWorkflowEvent[],
  maximumItems = 3,
): AgentWorkflowActivityItem[] {
  const latestActivityByNodeId = new Map<string, AgentWorkflowActivityItem>();
  for (const event of events) {
    const activity = agentWorkflowEventActivity(event);
    if (activity) latestActivityByNodeId.set(activity.nodeExecutionId, activity);
  }
  const latestActivities = [...latestActivityByNodeId.values()];
  const running = latestActivities
    .filter((activity) => activity.phase === "running")
    .sort((left, right) => left.eventSequence - right.eventSequence);
  const completed = latestActivities
    .filter((activity) => activity.phase !== "running")
    .sort((left, right) => right.eventSequence - left.eventSequence);
  return [...running, ...completed].slice(0, maximumItems);
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
