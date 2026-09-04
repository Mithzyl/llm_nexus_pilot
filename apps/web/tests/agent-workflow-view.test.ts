import assert from "node:assert/strict";
import test from "node:test";

import {
  agentDispatchGroupSummary,
  agentNodeBudgetSummary,
  agentNodeMissingOutputLabel,
  agentNodePublicActivityText,
  selectRecentAgentWorkflowActivities,
  isAgentWorkflowCancellable,
} from "../lib/agent-workflow-view";
import type { AgentWorkflowEvent } from "../lib/types";

/** Build one durable node event with an optional versioned public activity. */
function activityEvent(
  sequence: number,
  nodeExecutionId: string,
  phase: "running" | "completed",
  text: string,
): AgentWorkflowEvent {
  return {
    event_id: `event-${sequence}`,
    event_sequence: sequence,
    workflow_execution_id: "workflow-1",
    run_id: "run-1",
    node_execution_id: nodeExecutionId,
    event_type: phase === "running" ? "agent.node.started" : "agent.node.completed",
    workflow_status: "running",
    node_status: phase,
    occurred_at: "2026-09-02T00:00:00Z",
    public_summary: text,
    public_payload: {
      activity: {
        schema_version: "node_activity_summary.v1",
        phase,
        text,
        source: phase === "running" ? "deterministic_input" : "typed_output",
      },
    },
    trace_id: null,
  };
}

test("describes every dispatch group without guessing order from event arrival", () => {
  assert.equal(
    agentDispatchGroupSummary([
      { group_id: "group-1", agent_run_ids: ["agent-1", "agent-2"], concurrency_limit: 2 },
      { group_id: "group-2", agent_run_ids: ["agent-3"], concurrency_limit: 1 },
    ]),
    "group-1 · 2 个 Agent · 并发 2；group-2 · 1 个 Agent · 并发 1",
  );
});

test("keeps consumed cost and temporary reservation as separate budget facts", () => {
  assert.deepEqual(
    agentNodeBudgetSummary({ cost_used_after: "1.25", reserved_estimated_cost: "3.00" }),
    [
      ["已消费费用", "1.25"],
      ["当前费用预留", "3.00"],
    ],
  );
});

test("offers explicit cancellation only for a running Workflow", () => {
  assert.equal(isAgentWorkflowCancellable("running"), true);
  assert.equal(isAgentWorkflowCancellable("pending"), false);
  assert.equal(isAgentWorkflowCancellable("cancelled"), false);
  assert.equal(isAgentWorkflowCancellable("completed"), false);
});

test("explains missing node output from the durable node status", () => {
  assert.equal(agentNodeMissingOutputLabel("running"), "节点正在运行，尚未提交输出");
  assert.equal(agentNodeMissingOutputLabel("failed"), "节点执行失败，未生成可提交输出");
  assert.equal(agentNodeMissingOutputLabel("cancelled"), "节点已取消，未生成可提交输出");
  assert.equal(agentNodeMissingOutputLabel("completed"), "节点已完成，但提交的输出事实缺失");
});

test("prefers versioned node activity while preserving historical summary fallback", () => {
  assert.equal(
    agentNodePublicActivityText({
      status_label: "running",
      summary: "legacy summary",
      progress_current: 1,
      progress_total: 11,
      activity: {
        schema_version: "node_activity_summary.v1",
        phase: "running",
        text: "正在制定执行计划",
        source: "deterministic_input",
      },
    }),
    "正在制定执行计划",
  );
  assert.equal(
    agentNodePublicActivityText({
      status_label: "completed",
      summary: "legacy summary",
      progress_current: 1,
      progress_total: 11,
    }),
    "legacy summary",
  );
});

test("keeps parallel running activities and then fills recent completed facts", () => {
  const activities = selectRecentAgentWorkflowActivities([
    activityEvent(1, "worker-1", "running", "正在处理任务一"),
    activityEvent(2, "worker-2", "running", "正在处理任务二"),
    activityEvent(3, "worker-1", "completed", "已完成任务一"),
    activityEvent(4, "handoff-1", "completed", "已收到任务一交接"),
  ]);

  assert.deepEqual(
    activities.map((activity) => [activity.nodeExecutionId, activity.text]),
    [
      ["worker-2", "正在处理任务二"],
      ["handoff-1", "已收到任务一交接"],
      ["worker-1", "已完成任务一"],
    ],
  );
});
