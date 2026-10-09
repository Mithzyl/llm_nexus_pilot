import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AgentWorkflowActivityFeed, AgentWorkflowPanel } from "../components/agent-workflow/AgentWorkflowPanel";
import {
  agentDispatchGroupSummary,
  agentNodeBudgetSummary,
  agentNodeMissingOutputLabel,
  agentNodePublicActivityText,
  selectAgentWorkflowActivityTimeline,
  selectVisibleAgentWorkflowActivities,
  isAgentWorkflowAssistantMessage,
  isAgentWorkflowCancellable,
} from "../lib/agent-workflow-view";
import type { AgentWorkflowEvent, AgentWorkflowNodeResult, AgentWorkflowSummary } from "../lib/types";

/** Persisted reads and discovery remain visible even when the worker has no final output. */
test("renders committed tool results for a running or failed worker after reload", () => {
  const workflow: AgentWorkflowSummary = {
    workflow_execution_id: "wf", run_id: "run", workflow_name: "model_only",
    workflow_version: "1.0.0", execution_profile: "tool_enabled_v1", status: "running",
    version: 1, snapshot_version: 2, current_stage: "worker_execution",
    primary_node_execution_id: "node", active_node_execution_ids: ["node"],
    model_call_count: 2, max_model_calls: 16, node_count: 6, max_nodes: 24,
    started_at: null, completed_at: null, created_at: "", updated_at: "", error: null,
  };
  const node: AgentWorkflowNodeResult = {
    schema_version: "agent_workflow_node_result.v1", workflow_execution_id: "wf",
    workflow_name: "model_only", workflow_version: "1.0.0", node_execution_id: "node",
    node_key: "worker_execution.one", node_type: "model", node_version: "1.0.0",
    node_sequence: 6, node_attempt: 1, parent_node_execution_id: null, run_id: "run",
    task_id: "task", agent_run_id: "agent", agent_turn_id: null, agent_role: "planner",
    status: "running", output_type: "agent_model_execution",
    output_schema_version: "agent_model_execution.v1", output: null,
    input: { schema_version: "1", input_hash: "", source_node_execution_ids: [],
      message_ids: [], artifact_ids: [], handoff_ids: [], context_build_id: null, prompt_release_id: null },
    transition: { selected_transition: null, next_node_keys: [], skipped_node_keys: [], condition_summary: "" },
    evidence: { model_attempt_ids: [], tool_call_ids: ["call", "search", "describe"], evaluation_ids: [], artifact_ids: [],
      handoff_ids: [], tool_results: [{ tool_call_id: "call", tool_name: "read_file_content",
        version: "1.0.0", status: "completed", error_code: null, file_id: "file" },
        { tool_call_id: "search", tool_name: "search_tools", version: "1.0.0",
          status: "completed", error_code: null, file_id: null },
        { tool_call_id: "describe", tool_name: "describe_tools", version: "1.0.0",
          status: "completed", error_code: null, file_id: null }] },
    usage: { input_tokens: 30, output_tokens: 10, cached_tokens: 0, estimated_cost: "0",
      model_call_count: 1, tool_call_count: 3 },
    budget: { cost_limit: null, cost_used_before: "0", cost_used_after: "0", reserved_estimated_cost: "0",
      remaining_model_calls: 14, remaining_nodes: 18, remaining_wall_time_ms: 20000, remaining_cost: null },
    timing: { started_at: "", completed_at: null, duration_ms: 1 },
    public_view: { status_label: "running", summary: "工具结果已保存", progress_current: 6, progress_total: 11 },
    observability: { trace_id: null, span_id: null }, warnings: [], error: null, created_at: "",
  };
  for (const status of ["running", "failed"] as const) {
    const markup = renderToStaticMarkup(createElement(AgentWorkflowPanel, {
      workflow, nodes: [{ ...node, status }], events: [], hasEventGap: false,
    }));
    assert.match(markup, /附件读取工作流/);
    assert.match(markup, /读取文件文字/);
    assert.match(markup, /查找可用工具/);
    assert.match(markup, /查看工具说明/);
    assert.match(markup, /读取结果已保存/);
    assert.match(markup, /3 次工具调用/);
    assert.doesNotMatch(markup, /output_json|provider_continuation/);
  }
});

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

test("keeps every lifecycle activity in durable event order", () => {
  const activities = selectAgentWorkflowActivityTimeline([
    activityEvent(1, "worker-1", "running", "正在处理任务一"),
    activityEvent(2, "worker-2", "running", "正在处理任务二"),
    activityEvent(3, "worker-1", "completed", "Agent 一已提交阶段结果"),
    activityEvent(4, "handoff-1", "completed", "已收到任务一交接"),
  ]);

  assert.deepEqual(
    activities.map((activity) => [activity.nodeExecutionId, activity.text]),
    [
      ["worker-1", "正在处理任务一"],
      ["worker-2", "正在处理任务二"],
      ["worker-1", "Agent 一已提交阶段结果"],
      ["handoff-1", "已收到任务一交接"],
    ],
  );
});

test("collapses to the newest activity and expands without losing earlier summaries", () => {
  const activities = selectAgentWorkflowActivityTimeline([
    activityEvent(1, "controller", "completed", "Controller 已规划"),
    activityEvent(2, "worker", "running", "Agent 正在执行"),
    activityEvent(3, "worker", "completed", "Agent 已提交阶段结果"),
    activityEvent(4, "synthesis", "running", "正在规划最终答案"),
  ]);

  assert.deepEqual(
    selectVisibleAgentWorkflowActivities(activities, false).map((activity) => activity.text),
    ["正在规划最终答案"],
  );
  assert.deepEqual(
    selectVisibleAgentWorkflowActivities(activities, true).map((activity) => activity.text),
    ["Controller 已规划", "Agent 正在执行", "Agent 已提交阶段结果", "正在规划最终答案"],
  );
});

test("renders an accessible collapsed activity disclosure with only the newest summary", () => {
  const markup = renderToStaticMarkup(
    createElement(AgentWorkflowActivityFeed, {
      events: [
        activityEvent(1, "controller", "completed", "Controller 已规划旧摘要"),
        activityEvent(2, "worker", "running", "Agent 正在执行最新任务"),
      ],
    }),
  );

  assert.match(markup, /aria-expanded="false"/);
  assert.match(markup, /Agent 正在执行最新任务/);
  assert.doesNotMatch(markup, /Controller 已规划旧摘要/);
});

test("restores workflow presentation on the persisted assistant message for the same run", () => {
  assert.equal(
    isAgentWorkflowAssistantMessage(
      { role: "assistant", run_id: "run-1" },
      "run-1",
    ),
    true,
  );
  assert.equal(
    isAgentWorkflowAssistantMessage(
      { role: "user", run_id: "run-1" },
      "run-1",
    ),
    false,
  );
  assert.equal(
    isAgentWorkflowAssistantMessage(
      { role: "assistant", run_id: "run-2" },
      "run-1",
    ),
    false,
  );
});
