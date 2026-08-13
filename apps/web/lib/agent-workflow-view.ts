import type {
  AgentWorkflowNodeOutputMap,
  AgentWorkflowNodeResult,
  AgentWorkflowStatus,
} from "./types";

type AgentDispatchGroup = AgentWorkflowNodeOutputMap["agent_dispatch"]["dispatch_groups"][number];
type AgentNodeBudget = AgentWorkflowNodeResult["budget"];

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

/** Expose the cancel action only while the public Workflow status is running. */
export function isAgentWorkflowCancellable(status: AgentWorkflowStatus): boolean {
  return status === "running";
}
