"use client";

import { useId, useState } from "react";

import type {
  AgentWorkflowEvent,
  AgentWorkflowNodeResult,
  AgentWorkflowResult,
  AgentWorkflowStatus,
  AgentWorkflowSummary,
  ReasoningBlockSnapshot,
} from "../../lib/types";
import { MarkdownContent } from "../markdown/MarkdownContent";
import { formatDurationMs, formatTokenCount } from "../../lib/format";
import { ReasoningView } from "../reasoning/ReasoningView";
import {
  agentCandidateCoverageSummary,
  agentDelegationRoleLabel,
  agentDispatchGroupSummary,
  agentNodeBudgetSummary,
  agentNodeMissingOutputLabel,
  agentActivityDisplayText,
  agentNodePublicActivityText,
  agentToolLabel,
  agentToolResultSummary,
  agentToolStatusLabel,
  agentWorkflowEventActivity,
  groupAgentToolCallActivities,
  isAgentWorkflowCancellable,
  selectAgentWorkflowActivityTimeline,
  selectVisibleAgentWorkflowActivities,
} from "../../lib/agent-workflow-view";

const NODE_LABELS: Record<AgentWorkflowNodeResult["output_type"], string> = {
  general_candidate: "模型 Agent",
  general_delegated_candidate: "模型 Agent",
  general_review: "独立审核",
  request_intake: "请求归一化",
  context_assembly: "上下文组装",
  controller_plan: "Controller 规划",
  plan_validation: "计划校验",
  agent_dispatch: "Agent 分派",
  agent_model_execution: "模型 Agent",
  agent_handoff: "Handoff",
  deterministic_verification: "确定性验证",
  independent_review: "独立审核",
  final_synthesis: "最终汇总",
  workflow_completion: "工作流完成",
};

/** Convert workflow status values into stable user-facing labels without inventing actions. */
export function agentWorkflowStatusLabel(status: AgentWorkflowStatus): string {
  const labels: Record<AgentWorkflowStatus, string> = {
    pending: "等待执行",
    running: "正在运行",
    waiting_for_input: "等待输入（当前版本不可操作）",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    outcome_unknown: "结果未知",
  };
  return labels[status];
}

/** Format a bounded list for the compact node evidence drawer. */
function listValue(values: string[], emptyLabel = "无"): string {
  return values.length > 0 ? values.join("、") : emptyLabel;
}

/** Map legacy, main and delegated API outputs into the existing read-only detail rows. */
function nodeOutputFacts(node: AgentWorkflowNodeResult): Array<[string, string]> {
  if (!node.output) return [["输出", agentNodeMissingOutputLabel(node.status)]];

  switch (node.output_type) {
    case "general_candidate":
    case "general_delegated_candidate": {
      const delegated = node.output_type === "general_delegated_candidate";
      const facts: Array<[string, string]> = [
        [
          "角色",
          delegated
            ? agentDelegationRoleLabel(node.agent_role, node.node_key) ?? "委派"
            : "主 Agent",
        ],
        ["来源", Object.keys(node.output.sources).length > 0
          ? `${Object.keys(node.output.sources).length} 个`
          : "无"],
      ];
      const coverage = agentCandidateCoverageSummary(node.output);
      if (coverage) facts.push(["返回范围", coverage]);
      if (Array.isArray(node.output.units)) {
        facts.push(["候选单元", `${node.output.units.length} 个`]);
      }
      facts.push(["拒绝原因", listValue(node.output.validation_issues)]);
      return facts;
    }
    case "general_review":
      return [
        ["结论", node.output.verdict],
        ["审核摘要", node.output.feedback],
      ];
    case "request_intake":
      return [
        ["目标", node.output.normalized_objective],
        ["复杂度", node.output.complexity],
        ["验收条件", listValue(node.output.acceptance_criteria)],
        ["不可用能力", listValue(node.output.unavailable_capabilities)],
      ];
    case "context_assembly":
      return [
        ["消息来源", `${node.output.included_message_ids.length} 条`],
        ["输入字符", String(node.output.input_character_count)],
        ["截断", node.output.is_truncated ? "是" : "否"],
        ["排除来源", node.output.excluded_sources.map((source) => source.reason).join("；") || "无"],
      ];
    case "controller_plan":
      return [
        ["计划", node.output.decision_summary],
        ["任务", node.output.tasks.map((task) => `${task.title} · ${task.assigned_role}`).join("；")],
        ["审核策略", node.output.review_policy],
        ["已知风险", listValue(node.output.known_risks)],
      ];
    case "plan_validation":
      return [
        ["校验", node.output.is_valid ? "通过" : "拒绝"],
        ["拓扑顺序", listValue(node.output.topological_task_order)],
        ["任务数", String(node.output.total_task_count)],
        ["拒绝原因", node.output.rejected_plan_reason ?? "无"],
      ];
    case "agent_dispatch":
      return [
        ["Task", `${node.output.created_tasks.length} 个`],
        ["Agent Run", `${node.output.created_agent_runs.length} 个`],
        ["执行组", agentDispatchGroupSummary(node.output.dispatch_groups)],
        ["角色模型", node.output.role_model_bindings.map((item) => `${item.agent_role} · ${item.model}`).join("；")],
      ];
    case "agent_model_execution":
      return [
        ["Agent 摘要", node.output.structured_output.summary],
        ["Provider / Model", `${node.output.provider} / ${node.output.model}`],
        ["完成原因", node.output.finish_reason],
        ["时长", formatDurationMs(node.output.latency_ms)],
      ];
    case "agent_handoff":
      return [
        ["目标", node.output.objective],
        ["状态", node.output.status],
        ["确认事实", listValue(node.output.confirmed_facts)],
        ["剩余工作", listValue(node.output.remaining_work)],
      ];
    case "deterministic_verification":
      return [
        ["结论", node.output.verdict],
        ["覆盖", node.output.coverage_summary],
        ["阻塞发现", listValue(node.output.blocking_findings)],
        ["未验证主张", listValue(node.output.unverified_claims)],
      ];
    case "independent_review":
      return [
        ["结论", node.output.verdict],
        ["分数", String(node.output.score)],
        ["审核摘要", node.output.review_summary],
        ["缺失证据", listValue(node.output.missing_evidence)],
      ];
    case "final_synthesis": {
      const facts: Array<[string, string]> = [
        ["已完成目标", listValue(node.output.completed_objectives)],
        ["未解决事项", listValue(node.output.unresolved_items)],
        ["警告", listValue(node.output.warnings)],
        ["消息记录", node.output.assistant_message_id ? "已关联" : "未关联"],
      ];
      const delegatedAgentCount = node.output.source_agent_run_ids?.length ?? 0;
      const handoffCount = node.output.source_handoff_ids?.length ?? 0;
      if (delegatedAgentCount > 0) facts.push(["委派 Agent", String(delegatedAgentCount)]);
      if (handoffCount > 0) facts.push(["Handoff", String(handoffCount)]);
      return facts;
    }
    case "workflow_completion":
      return [
        ["节点", String(node.output.node_count)],
        ["模型调用", String(node.output.model_attempt_ids.length)],
        ["总 Token", formatTokenCount(node.output.total_input_tokens + node.output.total_output_tokens)],
        ["总耗时", formatDurationMs(node.output.total_duration_ms)],
      ];
  }
}

/** Format one durable activity time for the expanded workflow timeline. */
function formatAgentActivityTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

/** Present call counts without repeating one line per call. */
function AgentNodeToolSummary({ node }: { node: AgentWorkflowNodeResult }) {
  const results = node.evidence.tool_results ?? [];
  if (results.length === 0) return null;
  const summary = agentToolResultSummary(results);
  return <section className={`agent-tool-summary ${summary.hasIssues ? "has-issues" : ""}`} aria-label="工具结果摘要">
    <div className="agent-tool-summary-head">
      <strong>工具结果</strong>
      <span>{summary.total} 次调用 · {summary.statusLabel}</span>
    </div>
    <div className="agent-tool-summary-groups">
      {summary.groups.map((group) => <span key={group.label}>{group.label}<small>{group.count}</small></span>)}
    </div>
    <details className="agent-tool-details">
      <summary>查看 {summary.total} 次调用明细</summary>
      <ul>
        {results.map((call) => <li key={call.tool_call_id}>
          {agentToolLabel(call.tool_name)} · {agentToolStatusLabel(call.tool_name, call.status)}
          {call.error_code ? ` · ${call.error_code}` : ""}
        </li>)}
      </ul>
    </details>
  </section>;
}

/** Render the latest activity by default and preserve the full collapsible timeline. */
export function AgentWorkflowActivityFeed({ events }: { events: AgentWorkflowEvent[] }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const activityTimelineId = useId();
  const activities = groupAgentToolCallActivities(selectAgentWorkflowActivityTimeline(events));
  if (activities.length === 0) return null;
  const latestActivity = activities[activities.length - 1];
  const visibleActivities = selectVisibleAgentWorkflowActivities(activities, isExpanded);

  return (
    <section
      className="agent-activity-feed"
      aria-label="Agent 工作流执行过程"
      data-expanded={isExpanded}
    >
      <button
        type="button"
        className="agent-activity-toggle"
        aria-expanded={isExpanded}
        aria-controls={activityTimelineId}
        onClick={() => setIsExpanded((current) => !current)}
      >
        <span>执行过程</span>
        <small>{isExpanded ? `${activities.length} 条活动` : "查看完整过程"}</small>
        <i aria-hidden="true">{isExpanded ? "−" : "+"}</i>
      </button>
      <ol id={activityTimelineId} aria-live="polite" aria-atomic="false">
        {visibleActivities.map((activity, index) => {
          const previousActivity = visibleActivities[index - 1];
          const activityTime = formatAgentActivityTime(activity.occurredAt);
          const showTime =
            isExpanded &&
            (!previousActivity ||
              formatAgentActivityTime(previousActivity.occurredAt) !== activityTime);
          return (
            <li
              key={activity.eventSequence}
              data-phase={activity.phase}
              data-latest={activity.eventSequence === latestActivity.eventSequence}
              title={activityTime}
            >
              <i aria-hidden="true" />
              <span>{agentActivityDisplayText(activity.text)}</span>
              {showTime && <time dateTime={activity.occurredAt}>{activityTime}</time>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/** Render one complete Agent Workflow snapshot or its live event projection. */
export function AgentWorkflowPanel({
  workflow,
  events,
  nodes: liveNodes,
  reasoningBlocksByAttemptId = {},
  hasEventGap,
  isCancelling = false,
  onCancel,
}: {
  workflow: AgentWorkflowSummary | AgentWorkflowResult;
  events: AgentWorkflowEvent[];
  nodes: AgentWorkflowNodeResult[];
  reasoningBlocksByAttemptId?: Record<string, ReasoningBlockSnapshot[]>;
  hasEventGap: boolean;
  isCancelling?: boolean;
  onCancel?: () => void;
}) {
  const nodes = "nodes" in workflow ? workflow.nodes : liveNodes;

  return (
    <section className="agent-workflow" data-status={workflow.status} aria-label="Agent 工作流证据">
      <div className="agent-workflow-heading">
        <div>
          <p className="eyebrow">{["tool_enabled_v1", "general_tools_v1"].includes(workflow.execution_profile) ? "附件读取工作流" : "纯模型工作流"}</p>
          <h3>已记录 {workflow.node_count} 个节点</h3>
        </div>
        <span>{workflow.active_node_execution_ids.length > 0 ? `${workflow.active_node_execution_ids.length} 个节点正在运行` : "当前无运行中的节点"}</span>
      </div>

      {workflow.error && (
        <div className="agent-workflow-error" role="alert">
          <span className="agent-workflow-error-kicker">失败原因</span>
          <strong>{workflow.error.public_message}</strong>
          <span>{workflow.error.error_code} · {workflow.error.outcome_is_known ? "结果已知" : "结果未知"}</span>
        </div>
      )}

      {onCancel && isAgentWorkflowCancellable(workflow.status) && (
        <div className="agent-cancel-action">
          <p>取消只停止后续节点；已经开始的模型调用仍会完成事实收敛。</p>
          <button type="button" disabled={isCancelling} onClick={onCancel}>
            {isCancelling ? "正在取消…" : "取消工作流"}
          </button>
        </div>
      )}

      {hasEventGap && (
        <p className="agent-evidence-warning" role="status">
          事件序号存在缺口；当前界面已停止宣称实时完整，并以查询快照为准。
        </p>
      )}

      <div className="agent-workflow-section-title">
        <h4>节点时间线</h4>
      </div>

      {nodes.length > 0 ? (
        <ol className="agent-node-list">
          {nodes.map((node) => (
            <li key={node.node_execution_id} data-status={node.status}>
              <details>
                <summary title={agentNodePublicActivityText(node.public_view)}>
                  <i aria-hidden="true" />
                  <span>
                    <strong>{node.output_type === "general_delegated_candidate"
                      ? `模型 Agent · ${agentDelegationRoleLabel(node.agent_role, node.node_key) ?? "委派"}`
                      : NODE_LABELS[node.output_type]}</strong>
                  </span>
                  <time>{formatDurationMs(node.timing.duration_ms)}</time>
                </summary>
                <div className="agent-node-details">
                  {(node.output_type === "general_candidate"
                    || node.output_type === "general_delegated_candidate") && node.output && (
                    <div className="agent-node-prose" tabIndex={0} aria-label="候选正文">
                      <MarkdownContent content={node.output.text} preserveHtmlText />
                    </div>
                  )}
                  <dl>
                    {nodeOutputFacts(node).map(([label, value], index) => (
                      <div key={`${label}-${index}`}><dt>{label}</dt><dd>{value}</dd></div>
                    ))}
                    {agentNodeBudgetSummary(node.budget).map(([label, value]) => (
                      <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
                    ))}
                  </dl>
                  <AgentNodeToolSummary node={node} />
                  <p>
                    {node.usage.model_call_count} 次模型调用 · {formatTokenCount(node.usage.input_tokens + node.usage.output_tokens)} tokens ·
                    {node.usage.tool_call_count > 0 && ` ${node.usage.tool_call_count} 次工具调用 · `}
                    {node.budget.remaining_model_calls === null ? "未设调用次数上限" : `剩余 ${node.budget.remaining_model_calls} 次调用`}
                  </p>
                  {node.evidence.model_attempt_ids.flatMap(
                    (modelAttemptId) =>
                      reasoningBlocksByAttemptId[modelAttemptId] ?? [],
                  ).map((presentation) => (
                    <ReasoningView
                      key={presentation.block_id}
                      presentation={presentation}
                      mode="trajectory"
                    />
                  ))}
                  {node.warnings.length > 0 && <p>警告：{node.warnings.join("；")}</p>}
                  {node.error && <p className="error-text">{node.error.public_message}</p>}
                </div>
              </details>
            </li>
          ))}
        </ol>
      ) : (
        <ol className="agent-event-list" aria-label="Agent Workflow 实时事件">
          {events.map((event) => {
            const activity = agentWorkflowEventActivity(event);
            return (
              <li key={event.event_id}>
                <i aria-hidden="true" />
                <span><strong>{activity?.text ?? event.public_summary}</strong><small>{event.event_type}</small></span>
                <time>#{event.event_sequence}</time>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
