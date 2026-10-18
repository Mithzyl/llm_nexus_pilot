"use client";

import { useEffect, useState } from "react";
import { nexusFetch } from "../../lib/api";
import { claimVerdictLabel, selectClaimEntries, sourceBlockLocation, type ClaimEntry } from "../../lib/claim-evidence";
import type { AgentWorkflowNodeResult, ClaimSources } from "../../lib/types";

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
    <p>原文暂不可用。文件可能已删除、权限已变化，或读取失败。</p>
    <button type="button" onClick={() => setReload((value) => value + 1)}>重试读取</button>
  </div>;
  if (!sources) return <p className="claim-source-note" role="status">正在读取原文…</p>;
  if (!sources.sources.length) return <p className="claim-source-note">这条结论没有文件原文引用。</p>;
  return <div className="claim-originals">
    <h4>引用原文</h4>
    {sources.sources.map((source, index) => <section className="claim-source" key={index}>
      <strong className="claim-source-file">{source.filename}</strong>
      {source.blocks.map((block, blockIndex) => <div key={blockIndex}>
        <p className="claim-source-note">{sourceBlockLocation(block)} · {source.read_by === "worker" ? "Worker 读取" : "Reviewer 补读"}</p>
        {block.cells ? <div className="claim-table-scroll"><table><tbody><tr>{block.cells.map((cell, i) => <td key={i}>{cell}</td>)}</tr></tbody></table></div>
          : <blockquote>{block.text}</blockquote>}
      </div>)}
      <p className="claim-source-note">本次引用 {source.blocks.length} 块 · 文件已解析 {source.total_blocks} 块{source.has_more === true ? " · 本次结果仍有后续分页" : source.has_more === false ? " · 本次结果分页结束" : ""}</p>
      {(source.extraction_status !== "complete" || source.warnings.length > 0) && <p className="claim-source-warning">解析存在限制，已提取文字不代表原文件全部内容。{source.warnings.join("；")}</p>}
    </section>)}
    <p className="claim-source-note">引用有效 · 与本次文件快照一致</p>
  </div>;
}

/** Keep claim judgment and source validity distinct; expand only user-selected originals. */
export function ClaimEvidencePanel({ workflowId, nodes, running }: {
  workflowId: string; nodes: AgentWorkflowNodeResult[]; running: boolean;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const entries = selectClaimEntries(nodes);
  const versioned = nodes.some((n) => n.output_type === "agent_model_execution" && n.output?.structured_output.schema_version === "worker_evidence.v2");
  return <section className="claim-panel" aria-label="结论与来源">
    {!entries.length && <p className="claim-source-note">{running ? "工作流运行中，尚未提交可展示的结论。" : versioned ? "本次没有提交逐条结论，可在运行过程查看未解决事项。" : "此记录未保存逐条来源，可查看原有运行过程。"}</p>}
    {entries.map((entry) => {
      const key = `${entry.nodeId}:${entry.claim.claim_key}`;
      const open = expanded === key;
      return <article className="claim-item" key={key}>
        <button type="button" className="claim-toggle" aria-expanded={open} onClick={() => setExpanded(open ? null : key)}>
          <span className="claim-text">{entry.claim.text}</span>
          <span className="claim-toggle-meta"><span className="claim-verdict" data-verdict={entry.assessment?.verdict}>{claimVerdictLabel(entry)}</span><span className="claim-toggle-hint">{open ? "收起" : "展开"}</span></span>
        </button>
        {open && <div className="claim-body">
          <p className="claim-source-note">Worker · {entry.claim.source_type === "file" ? "文件结论" : entry.claim.source_type === "inference" ? "推测" : "一般知识"}</p>
          {entry.assessment && entry.assessment.explanation.length > 280
            ? <details className="claim-review-details"><summary>查看完整审核说明</summary><p className="claim-explanation">{entry.assessment.explanation}</p></details>
            : <p className="claim-explanation">{entry.assessment?.explanation ?? "尚无逐条审核意见；引用有效不代表结论已获支持。"}</p>}
          <ClaimOriginals workflowId={workflowId} entry={entry} />
        </div>}
      </article>;
    })}
  </section>;
}
