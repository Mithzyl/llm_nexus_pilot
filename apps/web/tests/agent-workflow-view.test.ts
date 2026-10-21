import assert from "node:assert/strict";
import test from "node:test";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AgentWorkflowActivityFeed, AgentWorkflowPanel } from "../components/agent-workflow/AgentWorkflowPanel";
import {
  agentCandidateCoverageSummary,
  agentDelegationRoleLabel,
  agentDispatchGroupSummary,
  agentNodeBudgetSummary,
  agentNodeMissingOutputLabel,
  agentNodePublicActivityText,
  agentActivityDisplayText,
  agentToolLabel,
  agentToolResultSummary,
  agentToolStatusLabel,
  agentWorkflowEventActivity,
  groupAgentToolCallActivities,
  selectAgentWorkflowActivityTimeline,
  selectVisibleAgentWorkflowActivities,
  isAgentWorkflowAssistantMessage,
  isAgentWorkflowCancellable,
  cancelledMessageStatusLabel,
} from "../lib/agent-workflow-view";
import type { AgentWorkflowEvent, AgentWorkflowNodeResult, AgentWorkflowSummary } from "../lib/types";

/** Server-confirmed cancellation must not claim that its final state is still pending. */
test("distinguishes confirmed cancellation from a locally aborted response", () => {
  assert.equal(cancelledMessageStatusLabel(true), "已取消");
  assert.equal(cancelledMessageStatusLabel(false), "已停止 · 等待服务端最终状态");
  assert.equal(cancelledMessageStatusLabel(undefined), "已停止 · 等待服务端最终状态");
});

/** Render saved tool facts and both new API node outputs without breaking legacy records. */
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
  // New API node types must render through the existing drawer without dropping facts.
  for (const [output_type, output, expected] of [
    ["general_candidate", { text: "Candidate <script>text</script>", sources: {}, validation_issues: [] }, "Candidate &lt;script&gt;text&lt;/script&gt;"],
    ["general_delegated_candidate", { text: "Delegated prose", sources: {}, validation_issues: [] }, "Delegated prose"],
    ["general_review", { verdict: "unconfirmed", feedback: "Review needs evidence" }, "Review needs evidence"],
  ] as const) {
    const markup = renderToStaticMarkup(createElement(AgentWorkflowPanel, {
      workflow: { ...workflow, execution_profile: "general_tools_v1" },
      nodes: [{ ...node, status: "completed", output_type, output } as unknown as AgentWorkflowNodeResult],
      events: [], hasEventGap: false,
    }));
    assert.ok(markup.includes(expected));
    assert.match(markup, /附件读取工作流/);
    assert.doesNotMatch(markup, /<script>/);
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
      ["当前费用预留", "3"],
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

test("names delegated roles and task tools without claiming unauthorized access", () => {
  assert.equal(agentDelegationRoleLabel("planner", "general_delegate.task"), "Planner");
  assert.equal(agentDelegationRoleLabel("researcher", "general_delegate.task"), "Researcher");
  assert.equal(agentDelegationRoleLabel(null, "general_delegate.task"), "委派");
  assert.equal(agentDelegationRoleLabel(null, "general_agent"), null);

  assert.equal(agentToolLabel("delegate_task"), "委派子任务");
  assert.equal(agentToolLabel("save_task_note"), "保存任务笔记");
  assert.equal(agentToolLabel("read_task_note"), "读取任务笔记");
  assert.equal(agentToolLabel("list_task_notes"), "列出任务笔记");
  assert.equal(agentToolLabel("get_task_read_coverage"), "查看阅读进度");
  assert.equal(agentToolLabel("unknown_tool"), "未知工具");

  assert.equal(agentToolStatusLabel("delegate_task", "completed"), "委派完成");
  assert.equal(agentToolStatusLabel("delegate_task", "permission_denied"), "未获准委派");
  assert.equal(agentToolStatusLabel("save_task_note", "completed"), "已保存");
  assert.equal(agentToolStatusLabel("get_task_read_coverage", "completed"), "已返回范围");
  assert.equal(agentToolStatusLabel("read_file_content", "completed"), "读取结果已保存");
  assert.equal(agentToolStatusLabel("unknown_tool", "completed"), "状态待确认");
});

test("summarizes returned blocks without claiming full understanding", () => {
  const base = {
    schema_version: "general_candidate.v2" as const,
    text: "",
    sources: {},
    validation_issues: [],
  };
  const partial = {
    ...base,
    read_coverage: [{
      file_id: "file", filename: "paper.pdf", source_hash: "s", parsed_hash: "p",
      parser_version: "1", source_aliases: ["S1"], total_parsed_blocks: 100,
      returned_block_ids: ["b1", "b2"], unreturned_block_count: 98,
      all_parsed_blocks_returned: false, extraction_status: "complete",
      warnings: [], model_understanding_verified: false as const,
    }],
  };
  assert.equal(agentCandidateCoverageSummary(partial), "1 个文件 · 2 / 100 块");
  assert.equal(
    agentCandidateCoverageSummary({
      ...partial,
      read_coverage: [{ ...partial.read_coverage[0], returned_block_ids: Array.from({ length: 100 }, (_, i) => `b${i}`), all_parsed_blocks_returned: true }],
    }),
    "1 个文件 · 全部返回",
  );
  assert.equal(agentCandidateCoverageSummary(base), null);
});

test("surfaces delegation revisions without inventing a node activity payload", () => {
  const event = {
    event_id: "event-delegation", event_sequence: 9, workflow_execution_id: "workflow",
    run_id: "run", node_execution_id: "delegated-node",
    event_type: "agent.delegation.revision.requested", workflow_status: "running",
    node_status: "running", occurred_at: "2026-10-19T00:00:00Z",
    public_summary: "候选回答需要修订，继续由原 Agent 处理", public_payload: {}, trace_id: null,
  } as const;
  const activity = agentWorkflowEventActivity(event as AgentWorkflowEvent);
  assert.equal(activity?.text, "候选回答需要修订，继续由原 Agent 处理");
  assert.equal(activity?.phase, "running");
  assert.equal(selectAgentWorkflowActivityTimeline([event as AgentWorkflowEvent]).length, 1);
});

test("renders delegated candidate role, coverage, tool effect and bounded prose", () => {
  const workflow: AgentWorkflowSummary = {
    workflow_execution_id: "wf", run_id: "run", workflow_name: "model_only",
    workflow_version: "1.0.0", execution_profile: "general_tools_v1", status: "completed",
    version: 1, snapshot_version: 2, current_stage: "workflow_completion",
    primary_node_execution_id: "delegated", active_node_execution_ids: [],
    model_call_count: 2, max_model_calls: 16, node_count: 2, max_nodes: 24,
    started_at: null, completed_at: null, created_at: "", updated_at: "", error: null,
  };
  const node = {
    schema_version: "agent_workflow_node_result.v1", workflow_execution_id: "wf",
    workflow_name: "model_only", workflow_version: "1.0.0", node_execution_id: "delegated",
    node_key: "general_delegate.task-1", node_type: "model", node_version: "1.0.0",
    node_sequence: 1, node_attempt: 1, parent_node_execution_id: "parent", run_id: "run",
    task_id: "task", agent_run_id: "agent", agent_turn_id: null, agent_role: "planner",
    status: "completed", output_type: "general_delegated_candidate",
    output_schema_version: "general_candidate.v2",
    output: {
      schema_version: "general_candidate.v2", text: "## 委派摘要\n\n**Delegated** candidate body\n\n- 第一点\n- 第二点",
      sources: { S1: { tool_call_id: "read", result_id: "r", file_id: "f", block_ids: ["b1"], source_hash: "s", parsed_hash: "p", parser_version: "1" } },
      validation_issues: [],
      units: [{ unit_id: "u1", kind: "prose", start_char: 0, end_char: 3, text: "body", source_aliases: ["S1"], unknown_source_aliases: [], citation_status: "present", content_verified: false }],
      read_coverage: [{ file_id: "f", filename: "paper.pdf", source_hash: "s", parsed_hash: "p", parser_version: "1", source_aliases: ["S1"], total_parsed_blocks: 100, returned_block_ids: ["b1", "b2"], unreturned_block_count: 98, all_parsed_blocks_returned: false, extraction_status: "complete", warnings: [], model_understanding_verified: false }],
    },
    input: { schema_version: "1", input_hash: "", source_node_execution_ids: [], message_ids: [], artifact_ids: [], handoff_ids: [], context_build_id: null, prompt_release_id: null },
    transition: { selected_transition: null, next_node_keys: [], skipped_node_keys: [], condition_summary: "" },
    evidence: { model_attempt_ids: [], tool_call_ids: ["delegate"], evaluation_ids: [], artifact_ids: [], handoff_ids: [], tool_results: [{ tool_call_id: "delegate", tool_name: "delegate_task", version: "1.0.0", status: "completed", error_code: null, file_id: null }] },
    usage: { input_tokens: 10, output_tokens: 5, cached_tokens: 0, estimated_cost: "0", model_call_count: 1, tool_call_count: 1 },
    budget: { cost_limit: null, cost_used_before: "0", cost_used_after: "0", reserved_estimated_cost: "0", remaining_model_calls: 15, remaining_nodes: 23, remaining_wall_time_ms: 10000, remaining_cost: null },
    timing: { started_at: "", completed_at: "", duration_ms: 18 },
    public_view: { status_label: "completed", summary: "Delegated work", progress_current: 1, progress_total: 1 },
    observability: { trace_id: null, span_id: null }, warnings: [], error: null, created_at: "",
  } as unknown as AgentWorkflowNodeResult;
  const finalNode = {
    ...node,
    node_execution_id: "final", node_key: "final_synthesis", node_sequence: 2,
    agent_role: "controller", output_type: "final_synthesis",
    output: {
      answer_type: "text", final_text: "answer", completed_objectives: [], unresolved_items: [],
      warnings: [], recommended_next_actions: [], source_agent_run_ids: ["agent"], source_handoff_ids: ["handoff"],
      source_artifact_ids: [], source_evaluation_ids: [], assistant_message_id: "message",
    },
  } as unknown as AgentWorkflowNodeResult;
  const markup = renderToStaticMarkup(createElement(AgentWorkflowPanel, {
    workflow, nodes: [node, finalNode], events: [], hasEventGap: false,
  }));
  assert.match(markup, /模型 Agent · Planner/);
  assert.match(markup, /委派子任务/);
  assert.match(markup, /委派完成/);
  assert.match(markup, /返回范围/);
  assert.match(markup, /1 个文件 · 2 \/ 100 块/);
  assert.match(markup, /候选单元/);
  assert.match(markup, /agent-node-prose/);
  assert.match(markup, /委派摘要/);
  assert.match(markup, /<strong>Delegated<\/strong> candidate body/);
  assert.match(markup, /<li>第一点<\/li>/);
  assert.match(markup, /委派 Agent/);
  assert.match(markup, /Handoff/);
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

test("summarizes tool calls by tool and completion status", () => {
  const results = [
    { tool_call_id: "a", tool_name: "read_file_content", version: "1", status: "completed", error_code: null, file_id: "f" },
    { tool_call_id: "b", tool_name: "read_file_content", version: "1", status: "completed", error_code: null, file_id: "f" },
    { tool_call_id: "c", tool_name: "search_file_content", version: "1", status: "failed", error_code: "timeout", file_id: "f" },
  ];
  const summary = agentToolResultSummary(results);
  assert.equal(summary.total, 3);
  assert.equal(summary.completed, 2);
  assert.equal(summary.statusLabel, "1 次未完成");
  assert.equal(summary.hasIssues, true);
  assert.deepEqual(summary.groups, [
    { label: "读取文件文字", count: 2 },
    { label: "搜索文件文字", count: 1 },
  ]);
});

test("groups consecutive tool progress activities without losing lifecycle events", () => {
  const activities = selectAgentWorkflowActivityTimeline([
    activityEvent(1, "node", "running", "已记录 1 次工具调用，Agent 继续处理"),
    activityEvent(2, "node", "running", "已记录 2 次工具调用，Agent 继续处理"),
    activityEvent(3, "node", "running", "已记录 3 次工具调用，Agent 继续处理"),
    activityEvent(4, "node", "completed", "Agent 已提交候选回答"),
  ]);
  const grouped = groupAgentToolCallActivities(activities);
  assert.deepEqual(grouped.map((activity) => activity.text), [
    "已记录 3 次工具调用，Agent 继续处理",
    "Agent 已提交候选回答",
  ]);
});

test("keeps detailed failure messages in the inspector", () => {
  assert.equal(
    agentActivityDisplayText("执行失败：Reviewer decision is invalid after one repair"),
    "执行失败，详情见运行详情",
  );
  assert.equal(
    agentActivityDisplayText("Agent 已提交候选回答"),
    "Agent 已提交候选回答",
  );
});
