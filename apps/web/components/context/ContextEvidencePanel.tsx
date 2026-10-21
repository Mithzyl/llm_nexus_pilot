import type { ContextBuildEvidence } from "../../lib/types";
import { formatTokenCount } from "../../lib/format";

type ContextEvidencePanelProps = {
  evidence: ContextBuildEvidence | null;
  state: "empty" | "loading" | "ready" | "error";
  errorMessage: string | null;
};

const SOURCE_LABELS: Record<string, string> = {
  safety_instruction: "安全指令",
  system_instruction: "系统指令",
  message: "会话消息",
  artifact: "Artifact",
  agent_handoff: "Agent 交接",
  agent_node: "Agent 节点",
  evaluation: "评估",
};

/** Keep content hashes compact in the summary; the full version remains on hover. */
function compactSourceVersion(version: string): string {
  return version.length > 8 ? `${version.slice(0, 8)}…` : version;
}

/** Render persisted Context selection metadata without source bodies or object locations. */
export function ContextEvidencePanel({
  evidence,
  state,
  errorMessage,
}: ContextEvidencePanelProps) {
  const browserVisibleSources =
    evidence?.sources.filter((source) => source.source_type !== "prompt_release") ?? [];
  const selectedCount =
    browserVisibleSources.filter((source) => source.selection_status === "selected").length;
  const excludedCount =
    browserVisibleSources.filter((source) => source.selection_status === "excluded").length;

  return (
    <section className="inspector-section context-evidence-section">
      <div className="section-title">
        <h3>模型上下文</h3>
        {state !== "empty" && <span>
          {state === "loading"
            ? "读取中"
            : state === "error"
              ? "不可用"
              : evidence
                ? `${selectedCount} 纳入 · ${excludedCount} 排除`
                : "等待调用"}
        </span>}
      </div>
      {state === "error" ? (
        <p className="context-evidence-error">{errorMessage ?? "上下文证据暂不可用。"}</p>
      ) : evidence ? (
        <>
          <dl className="context-evidence-summary">
            <div><dt>策略</dt><dd>{evidence.policy_version}</dd></div>
            <div><dt>输入估算</dt><dd>{formatTokenCount(evidence.input_token_estimate)} tokens</dd></div>
            <div><dt>模型目录</dt><dd>{evidence.catalog_version_id.slice(0, 8)}</dd></div>
          </dl>
          <ul className="context-source-list">
            {browserVisibleSources.map((source) => {
              const selectionLabel =
                source.selection_status === "selected"
                  ? `${formatTokenCount(source.token_estimate)} tokens`
                  : source.exclusion_reason ?? "已排除";
              const sourceStatus = `${selectionLabel}${source.is_required ? " · 必选" : ""}`;
              const sourceVersion = source.source_version
                ? compactSourceVersion(source.source_version)
                : null;
              return (
                <li key={`${source.source_type}-${source.source_id}`}>
                  <div>
                    <strong>{SOURCE_LABELS[source.source_type] ?? source.source_type}</strong>
                    <small title={source.source_version ?? undefined}>
                      {source.trust_level}
                      {sourceVersion ? ` · v${sourceVersion}` : ""}
                    </small>
                  </div>
                  <span className={source.selection_status} title={sourceStatus}>
                    {sourceStatus}
                  </span>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
    </section>
  );
}
