import type { ContextBuildEvidence } from "../../lib/types";

type ContextEvidencePanelProps = {
  evidence: ContextBuildEvidence | null;
  state: "empty" | "loading" | "ready" | "error";
  errorMessage: string | null;
};

const SOURCE_LABELS: Record<string, string> = {
  safety_instruction: "安全指令",
  system_instruction: "系统指令",
  message: "会话消息",
  prompt_release: "Prompt",
  artifact: "Artifact",
  agent_handoff: "Agent 交接",
  agent_node: "Agent 节点",
  evaluation: "评估",
};

/** Render persisted Context selection metadata without source bodies or object locations. */
export function ContextEvidencePanel({
  evidence,
  state,
  errorMessage,
}: ContextEvidencePanelProps) {
  const selectedCount =
    evidence?.sources.filter((source) => source.selection_status === "selected").length ?? 0;
  const excludedCount =
    evidence?.sources.filter((source) => source.selection_status === "excluded").length ?? 0;

  return (
    <section className="inspector-section context-evidence-section">
      <div className="section-title">
        <h3>模型上下文</h3>
        <span>
          {state === "loading"
            ? "读取中"
            : state === "error"
              ? "不可用"
              : evidence
                ? `${selectedCount} 纳入 · ${excludedCount} 排除`
                : "等待调用"}
        </span>
      </div>
      {state === "error" ? (
        <p className="context-evidence-error">{errorMessage ?? "上下文证据暂不可用。"}</p>
      ) : evidence ? (
        <>
          <dl className="context-evidence-summary">
            <div><dt>策略</dt><dd>{evidence.policy_version}</dd></div>
            <div><dt>输入估算</dt><dd>{evidence.input_token_estimate} tokens</dd></div>
            <div><dt>模型目录</dt><dd>{evidence.catalog_version_id.slice(0, 8)}</dd></div>
          </dl>
          <ul className="context-source-list">
            {evidence.sources.map((source) => (
              <li key={`${source.source_type}-${source.source_id}`}>
                <div>
                  <strong>{SOURCE_LABELS[source.source_type] ?? source.source_type}</strong>
                  <small>
                    {source.trust_level}
                    {source.source_version ? ` · v${source.source_version}` : ""}
                  </small>
                </div>
                <span className={source.selection_status}>
                  {source.selection_status === "selected"
                    ? `${source.token_estimate} tokens`
                    : source.exclusion_reason ?? "已排除"}
                  {source.is_required ? " · 必选" : ""}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="context-evidence-empty">模型调用后显示实际纳入与排除来源。</p>
      )}
    </section>
  );
}
