"use client";

import { useEffect, useState } from "react";
import { nexusFetch } from "../../lib/api";
import {
  claimEvidenceActionLabel,
  claimSourceKindLabel,
  claimVerdictLabel,
  safeWebSourceUrl,
  selectClaimEntries,
  sourceBlockLocation,
  visibleClaimBlocks,
  webSourceDisplayTitle,
  type ClaimCitationTarget,
  type ClaimEntry,
} from "../../lib/claim-evidence";
import type { AgentWorkflowNodeResult, ClaimSources } from "../../lib/types";
import { MarkdownContent } from "../markdown/MarkdownContent";

/** Fetch only expanded excerpts; clear them on focus refresh and discard stale async responses. */
function ClaimOriginals({ workflowId, entry }: { workflowId: string; entry: ClaimEntry }) {
  const [sources, setSources] = useState<ClaimSources | null>(null);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const reviewReferences = JSON.stringify(entry.assessment?.evidence_refs ?? []);
  useEffect(() => {
    const controller = new AbortController();
    let sequence = 0;
    /** Recheck authorization instead of restoring previously fetched source text. */
    function refresh() {
      const request = ++sequence;
      setSources(null);
      setFailed(false);
      void nexusFetch<ClaimSources>(
        `agent-workflows/${encodeURIComponent(workflowId)}/nodes/${encodeURIComponent(entry.nodeId)}/claim-sources?claim_key=${encodeURIComponent(entry.claim.claim_key)}`,
        { signal: controller.signal },
      ).then((value) => { if (!controller.signal.aborted && request === sequence) setSources(value); })
        .catch(() => { if (!controller.signal.aborted && request === sequence) setFailed(true); });
    }
    /** Drop hidden source text and revalidate when the browser becomes visible again. */
    function visibility() {
      if (document.hidden) { sequence++; setSources(null); }
      else refresh();
    }
    refresh();
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      controller.abort();
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [workflowId, entry.nodeId, entry.claim.claim_key, reviewReferences, reload]);
  if (failed) return <div className="claim-source-error" role="alert">
    <p>来源暂不可用。文件或网页可能已失效、权限已变化，或读取失败。</p>
    <button type="button" onClick={() => setReload((value) => value + 1)}>重试读取</button>
  </div>;
  if (!sources) return <p className="claim-source-note" role="status">正在读取原文…</p>;
  if (!sources.sources.length) return <p className="claim-source-note">这条结论没有可读取的原文或网页依据。</p>;
  return <div className="claim-originals">
    <h4>引用原文</h4>
    {sources.sources.map((source, index) => {
      const isWebSource = source.source_kind === "web";
      const blocks = visibleClaimBlocks(source.blocks);
      const omittedBlankBlocks = source.blocks.length - blocks.length;
      const sourceUrl = safeWebSourceUrl(source.url);
      return <section className="claim-source" data-source-kind={isWebSource ? "web" : "file"} key={index}>
        <div className="claim-source-heading">
          <strong className="claim-source-file">
            {isWebSource ? webSourceDisplayTitle(source) : source.filename}
          </strong>
          <span>{isWebSource ? "网页摘录" : `${blocks.length} 块`}</span>
        </div>
        {isWebSource && sourceUrl && (
          <a className="claim-source-url" href={sourceUrl} target="_blank" rel="noopener noreferrer">
            {sourceUrl}
          </a>
        )}
        {blocks.length > 0
          ? blocks.map((block, blockIndex) => <div key={blockIndex}>
            {isWebSource && (block.title || block.url) && (
              <p className="claim-source-block-title">
                {webSourceDisplayTitle(source, block)}
                {safeWebSourceUrl(block.url) && (
                  <a href={safeWebSourceUrl(block.url) as string}
                    target="_blank" rel="noopener noreferrer">打开网页 ↗</a>
                )}
              </p>
            )}
            <p className="claim-source-note">
              {isWebSource ? "网页片段" : sourceBlockLocation(block)}
              {" · "}
              {source.read_by === "worker" ? "Worker 读取" : "Reviewer 补读"}
            </p>
            {block.cells ? <div className="claim-table-scroll"><table><tbody><tr>{block.cells.map((cell, i) => <td key={i}>{cell}</td>)}</tr></tbody></table></div>
              : <blockquote>{block.text}</blockquote>}
          </div>)
          : <p className="claim-source-note claim-source-empty">引用的位置是空白行，未显示空块。</p>}
        <p className="claim-source-note">
          {isWebSource
            ? `本次展示 ${blocks.length} 个网页片段`
            : `本次展示 ${blocks.length} 块${omittedBlankBlocks > 0 ? ` · 省略 ${omittedBlankBlocks} 个空白行` : ""} · 提取结果共 ${source.total_blocks} 块`}
          {source.has_more === true ? " · 仍有后续分页" : source.has_more === false ? " · 本次读取分页结束" : ""}
        </p>
        {(source.extraction_status !== "complete" || source.warnings.length > 0) && (
          <p className="claim-source-warning">
            {isWebSource
              ? "网页摘录来自搜索摘要或页面读取，当前接口未返回二者的明确类型；不代表完整正文，也不保证内容长期不变。"
              : "解析存在限制，已提取文字不代表原文件全部内容。"}
            {source.warnings.join("；")}
          </p>
        )}
      </section>;
    })}
    <p className="claim-source-note">
      {sources.sources.some((source) => source.source_kind === "web")
        ? "网页摘录来自搜索摘要或页面读取，当前接口未返回二者明确类型；不代表完整正文、长期不变或语义支持。"
        : "读取记录有效 · 未据此扩大为全文理解或语义确认"}
    </p>
  </div>;
}

/** Render adapted source records with a compact Markdown claim and progressive evidence. */
export function ClaimEvidencePanel({ workflowId, nodes, running, activeCitation }: {
  workflowId: string;
  nodes: AgentWorkflowNodeResult[];
  running: boolean;
  activeCitation?: ClaimCitationTarget | null;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const entries = selectClaimEntries(nodes);
  const versioned = nodes.some((n) =>
    (n.output_type === "agent_model_execution" && n.output?.structured_output.schema_version === "worker_evidence.v2") ||
    (n.output_type === "general_candidate" && n.output?.schema_version === "general_candidate.v2"));
  useEffect(() => {
    if (!activeCitation) return;
    const key = `${activeCitation.nodeId}:${activeCitation.claimKey}`;
    let scrollTimer: number | null = null;
    const frame = window.requestAnimationFrame(() => {
      setExpanded(key);
      setHighlighted(key);
      scrollTimer = window.setTimeout(() => {
        document.getElementById(`claim-${workflowId}-${key}`)?.scrollIntoView({
          block: "center",
          behavior: "smooth",
        });
      }, 0);
    });
    const timer = window.setTimeout(() => setHighlighted(null), 1600);
    return () => {
      window.cancelAnimationFrame(frame);
      if (scrollTimer !== null) window.clearTimeout(scrollTimer);
      window.clearTimeout(timer);
    };
  }, [activeCitation, workflowId]);
  return <section className="claim-panel" aria-label={`结论与来源，共 ${entries.length} 条`}>
    {!entries.length && <p className="claim-source-note">{running ? "工作流运行中，尚未提交可展示的结论。" : versioned ? "本次没有提交逐条结论，可在运行过程查看未解决事项。" : "此记录未保存逐条来源，可查看原有运行过程。"}</p>}
    {entries.map((entry, index) => {
      const key = `${entry.nodeId}:${entry.claim.claim_key}`;
      const open = expanded === key;
      const bodyId = `claim-body-${index}`;
      return <article
        className={`claim-item ${open ? "is-open" : ""} ${highlighted === key ? "is-citation-target" : ""}`}
        id={`claim-${workflowId}-${key}`}
        key={key}
      >
        <div className="claim-head">
          <div className="claim-text">
            <MarkdownContent content={entry.claim.text} preserveHtmlText />
          </div>
          <div className="claim-meta">
            <span className="claim-verdict" data-verdict={entry.assessment?.verdict}>{claimVerdictLabel(entry)}</span>
            <button
              type="button"
              className="claim-toggle"
              aria-expanded={open}
              aria-controls={bodyId}
              onClick={() => setExpanded(open ? null : key)}
            >
              {open ? "收起" : claimEvidenceActionLabel(entry.sourceKind)}
              <i aria-hidden="true">{open ? "−" : "+"}</i>
            </button>
          </div>
        </div>
        {open && <div className="claim-body" id={bodyId}>
          <p className="claim-source-note">
            Worker · {claimSourceKindLabel(entry.sourceKind)}
            {entry.sourceKind === "none" && entry.claim.source_type === "inference" ? " · 推测" : ""}
          </p>
          {entry.assessment && entry.assessment.explanation.length > 280
            ? <details className="claim-review-details"><summary>查看完整审核说明</summary><p className="claim-explanation">{entry.assessment.explanation}</p></details>
            : <p className="claim-explanation">{entry.assessment?.explanation ?? "尚无逐条审核意见；引用有效不代表结论已获支持。"}</p>}
          <ClaimOriginals workflowId={workflowId} entry={entry} />
        </div>}
      </article>;
    })}
  </section>;
}
