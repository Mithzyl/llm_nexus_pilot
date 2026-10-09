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
import { ReasoningView } from "../reasoning/ReasoningView";
import {
  agentDispatchGroupSummary,
  agentNodeBudgetSummary,
  agentNodeMissingOutputLabel,
  agentNodePublicActivityText,
  agentWorkflowEventActivity,
  isAgentWorkflowCancellable,
  selectAgentWorkflowActivityTimeline,
  selectVisibleAgentWorkflowActivities,
} from "../../lib/agent-workflow-view";

const NODE_LABELS: Record<AgentWorkflowNodeResult["output_type"], string> = {
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

const FILE_TOOL_LABELS: Record<string, string> = {
  search_tools: "查找可用工具",
  describe_tools: "查看工具说明",
  list_run_files: "列出附件",
  get_file_outline: "查看文件结构",
  search_file_content: "搜索文件文字",
  read_file_content: "读取文件文字",
  read_file_table: "读取表格",
};
const TOOL_STATUS_LABELS: Record<string, string> = {
  completed: "读取结果已保存", permission_denied: "未获授权或文件不可用",
  failed: "读取失败", timed_out: "读取超时", cancelled: "已取消",
  requested: "等待执行", running: "正在读取", outcome_unknown: "结果待确认",
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

/** Select concise, contract-specific facts from one complete node output. */
function nodeOutputFacts(node: AgentWorkflowNodeResult): Array<[string, string]> {
  if (!node.output) return [["输出", agentNodeMissingOutputLabel(node.status)]];

  switch (node.output_type) {
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
        ["时长", `${node.output.latency_ms} ms`],
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
    case "final_synthesis":
      return [
        ["已完成目标", listValue(node.output.completed_objectives)],
        ["未解决事项", listValue(node.output.unresolved_items)],
        ["警告", listValue(node.output.warnings)],
        ["消息记录", node.output.assistant_message_id ? "已关联" : "未关联"],
      ];
    case "workflow_completion":
      return [
        ["节点", String(node.output.node_count)],
        ["模型调用", String(node.output.model_attempt_ids.length)],
        ["总 Token", String(node.output.total_input_tokens + node.output.total_output_tokens)],
        ["总耗时", `${node.output.total_duration_ms} ms`],
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

/** Render the latest activity by default and preserve the full collapsible timeline. */
export function AgentWorkflowActivityFeed({ events }: { events: AgentWorkflowEvent[] }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const activityTimelineId = useId();
  const activities = selectAgentWorkflowActivityTimeline(events);
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
        {visibleActivities.map((activity) => (
          <li
            key={activity.eventSequence}
            data-phase={activity.phase}
            data-latest={activity.eventSequence === latestActivity.eventSequence}
          >
            <i aria-hidden="true" />
            <span>{activity.text}</span>
            {isExpanded && (
              <time dateTime={activity.occurredAt}>
                {formatAgentActivityTime(activity.occurredAt)}
              </time>
            )}
          </li>
        ))}
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
  const workflowResult = "nodes" in workflow ? workflow : null;

  return (
    <section className="agent-workflow" aria-label="Agent 工作流证据">
      <div className="agent-workflow-heading">
        <div>
          <p className="eyebrow">{workflow.execution_profile === "tool_enabled_v1" ? "附件读取工作流" : "纯模型工作流"}</p>
          <h3>{agentWorkflowStatusLabel(workflow.status)}</h3>
        </div>
        <span>{workflow.current_stage ?? "等待首个节点"}</span>
      </div>

      <dl className="agent-workflow-metrics">
        <div><dt>节点</dt><dd>{workflow.node_count} / {workflow.max_nodes}</dd></div>
        <div><dt>模型调用</dt><dd>{workflow.model_call_count} / {workflow.max_model_calls}</dd></div>
        <div><dt>活动节点</dt><dd>{workflow.active_node_execution_ids.length}</dd></div>
        <div><dt>快照</dt><dd>v{workflow.snapshot_version}</dd></div>
      </dl>

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

      {workflow.error && (
        <div className="agent-workflow-error" role="alert">
          <strong>{workflow.error.public_message}</strong>
          <span>{workflow.error.error_code} · {workflow.error.outcome_is_known ? "结果已知" : "结果未知"}</span>
        </div>
      )}

      <div className="agent-workflow-section-title">
        <h4>节点时间线</h4>
        <span>{nodes.length > 0 ? `${nodes.length} 个完整节点` : `${events.length} 个公共事件`}</span>
      </div>

      {nodes.length > 0 ? (
        <ol className="agent-node-list">
          {nodes.map((node) => (
            <li key={node.node_execution_id} data-status={node.status}>
              <details>
                <summary>
                  <i aria-hidden="true" />
                  <span>
                    <strong>{NODE_LABELS[node.output_type]}</strong>
                    <small>{agentNodePublicActivityText(node.public_view)}</small>
                  </span>
                  <time>{node.timing.duration_ms} ms</time>
                </summary>
                <div className="agent-node-details">
                  <dl>
                    {nodeOutputFacts(node).map(([label, value], index) => (
                      <div key={`${label}-${index}`}><dt>{label}</dt><dd>{value}</dd></div>
                    ))}
                    {agentNodeBudgetSummary(node.budget).map(([label, value]) => (
                      <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
                    ))}
                  </dl>
                  {(node.evidence.tool_results ?? []).length > 0 && (
                    <ul aria-label="工具调用">
                      {node.evidence.tool_results?.map((call) => (
                        <li key={call.tool_call_id}>
                          {FILE_TOOL_LABELS[call.tool_name] ?? "未授权工具"}
                          {" · "}{TOOL_STATUS_LABELS[call.status] ?? "状态待确认"}
                        </li>
                      ))}
                    </ul>
                  )}
                  <p>
                    {node.usage.model_call_count} 次模型调用 · {node.usage.input_tokens + node.usage.output_tokens} tokens ·
                    {node.usage.tool_call_count > 0 && ` ${node.usage.tool_call_count} 次工具调用 · `}
                    剩余 {node.budget.remaining_model_calls} 次调用
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

      {workflowResult?.final_output && (
        <div className="agent-final-evidence">
          <strong>最终结果已提交</strong>
          <span>{workflowResult.final_output.assistant_message_id ? "会话消息已关联" : "未关联会话消息"}</span>
        </div>
      )}
    </section>
  );
}
