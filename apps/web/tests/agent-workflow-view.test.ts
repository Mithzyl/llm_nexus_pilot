import assert from "node:assert/strict";
import test from "node:test";

import {
  agentDispatchGroupSummary,
  agentNodeBudgetSummary,
  agentNodeMissingOutputLabel,
  isAgentWorkflowCancellable,
} from "../lib/agent-workflow-view";

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
