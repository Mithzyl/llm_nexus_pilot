"use client";

import { useId, useState } from "react";
import type { ContextBuildEvidence } from "../../lib/types";
import { formatTokenCount } from "../../lib/format";

type ContextEvidencePanelProps = {
  evidence: ContextBuildEvidence | null;
  state: "empty" | "loading" | "ready" | "error";
  errorMessage: string | null;
  cumulativeInputTokens?: number | null;
};

const SOURCE_LABELS: Record<string, string> = {
  safety_instruction: "安全指令",
  system_instruction: "系统指令",
  message: "会话消息",
  artifact: "附件内容",
  parsed_artifact: "附件内容",
  agent_handoff: "Agent 交接",
  agent_node: "Agent 节点",
  evaluation: "评估",
  request_input: "请求输入",
  runtime_input: "运行时输入",
};

/** Keep content hashes compact in the summary; the full version remains on hover. */
function compactSourceVersion(version: string): string {
  return version.length > 8 ? `${version.slice(0, 8)}…` : version;
}

type ContextTokenPart = { label: string; tokens: number };

/** Combine selected sources by human label and keep only the largest contributors. */
function tokenComposition(evidence: ContextBuildEvidence): ContextTokenPart[] {
  const selected = evidence.sources.filter(
    (source) => source.source_type !== "prompt_release" && source.selection_status === "selected",
  );
  const byLabel = new Map<string, number>();
  for (const source of selected) {
    const label = SOURCE_LABELS[source.source_type] ?? source.source_type;
    byLabel.set(label, (byLabel.get(label) ?? 0) + source.token_estimate);
  }
  const sorted = [...byLabel.entries()]
    .map(([label, tokens]) => ({ label, tokens }))
    .sort((left, right) => right.tokens - left.tokens);
  if (sorted.length <= 4) return sorted;
  const visible = sorted.slice(0, 3);
  const remaining = sorted.slice(3).reduce((total, part) => total + part.tokens, 0);
  return [ ...visible, { label: "其他", tokens: remaining } ];
}

/** Render persisted Context selection metadata without source bodies or object locations. */
export function ContextEvidencePanel({
  evidence,
  state,
  errorMessage,
  cumulativeInputTokens,
}: ContextEvidencePanelProps) {
  const [isDetailsOpen, setIsDetailsOpen] = useState(false);
  const detailsId = useId();
  const browserVisibleSources =
    evidence?.sources.filter((source) => source.source_type !== "prompt_release") ?? [];
  const selectedCount =
    browserVisibleSources.filter((source) => source.selection_status === "selected").length;
  const excludedCount =
    browserVisibleSources.filter((source) => source.selection_status === "excluded").length;
  const composition = evidence ? tokenComposition(evidence) : [];
  const compositionTotal = composition.reduce((total, part) => total + part.tokens, 0);

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
                ? excludedCount > 0
                  ? `已纳入 ${selectedCount} 项 · 未纳入 ${excludedCount} 项`
                  : `已纳入 ${selectedCount} 项`
                : "等待调用"}
        </span>}
      </div>
      {state === "error" ? (
        <p className="context-evidence-error">{errorMessage ?? "上下文证据暂不可用。"}</p>
      ) : evidence ? (
        <>
          <div className="context-overview">
            <div className="context-total">
              <span>最近一次输入</span>
              <strong>{formatTokenCount(evidence.input_token_estimate)}</strong>
              <small>tokens</small>
            </div>
            <div className="context-composition-block">
              {compositionTotal > 0 && <div className="context-composition" role="img" aria-label="上下文 Token 构成">
                {composition.map((part, index) => (
                  <span
                    key={part.label}
                    data-tone={index}
                    style={{ width: `${(part.tokens / compositionTotal) * 100}%` }}
                    title={`${part.label} ${formatTokenCount(part.tokens)} tokens`}
                  />
                ))}
              </div>}
              <div className="context-composition-legend">
                {composition.map((part, index) => (
                  <span key={part.label} title={`${formatTokenCount(part.tokens)} tokens`}>
                    <i data-tone={index} aria-hidden="true" />
                    {part.label}
                    <small>{formatTokenCount(part.tokens)}</small>
                  </span>
                ))}
              </div>
            </div>
          </div>
          <div className="context-meta">
            {cumulativeInputTokens !== null && cumulativeInputTokens !== undefined && (
              <span>累计输入 <strong>{formatTokenCount(cumulativeInputTokens)}</strong> tokens</span>
            )}
          </div>
          <button
            type="button"
            className="context-details-toggle"
            aria-expanded={isDetailsOpen}
            aria-controls={detailsId}
            onClick={() => setIsDetailsOpen((current) => !current)}
          >
            <span>来源明细</span>
            <i aria-hidden="true">{isDetailsOpen ? "−" : "+"}</i>
          </button>
          <div
            id={detailsId}
            className={`context-details ${isDetailsOpen ? "is-open" : ""}`}
            aria-hidden={!isDetailsOpen}
            inert={!isDetailsOpen}
          >
            <div className="context-details-inner">
              <p className="context-technical">
                上下文策略 <strong>{evidence.policy_version}</strong> · 模型目录 <strong>{evidence.catalog_version_id.slice(0, 8)}</strong>
              </p>
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
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}
