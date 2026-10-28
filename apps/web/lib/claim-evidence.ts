import type { AgentWorkflowNodeResult, ClaimAssessment, ClaimSources, WorkerClaim } from "./types";

export type ClaimEntry = {
  nodeId: string;
  taskId: string;
  claim: WorkerClaim;
  sourceKind: "file" | "web" | "mixed" | "none";
  candidateStartChar?: number;
  candidateEndChar?: number;
  candidateKind?: "heading" | "prose" | "code";
  candidateSourceAliases?: string[];
  assessment?: Omit<ClaimAssessment, "verdict"> & {
    verdict: ClaimAssessment["verdict"] | "revise" | "unconfirmed";
  };
};

export type ClaimCitationTarget = {
  key: string;
  alias: string;
  nodeId: string;
  claimKey: string;
  /** JavaScript UTF-16 index in the rendered Markdown source. */
  contentStart: number;
  /** JavaScript UTF-16 end index, exclusive. */
  contentEnd: number;
};

/** Describe the persisted reference kinds without promoting them to semantic verification. */
function evidenceSourceKind(
  references: readonly ({ source_kind?: "file" | "web" } | undefined)[] | undefined,
): ClaimEntry["sourceKind"] {
  const kinds = new Set(
    (references ?? [])
      .filter((reference): reference is { source_kind?: "file" | "web" } => Boolean(reference))
      .map((reference) => reference.source_kind ?? "file"),
  );
  if (kinds.size === 0) return "none";
  if (kinds.size > 1) return "mixed";
  return kinds.has("web") ? "web" : "file";
}

/** Adapt legacy claims and the latest located candidate, binding review to its exact node. */
export function selectClaimEntries(nodes: AgentWorkflowNodeResult[]): ClaimEntry[] {
  const ordered = [...nodes].sort((a, b) => a.node_sequence - b.node_sequence);
  const candidates = ordered.filter((node) => node.output_type === "general_candidate" && node.status === "completed");
  const latest = candidates[candidates.length - 1];
  if (latest?.output_type === "general_candidate" && latest.output && latest.task_id) {
    const candidate = latest.output;
    const review = ordered.filter((node) => node.output_type === "general_review" && node.status === "completed" && node.output?.candidate_node_id === latest.node_execution_id).at(-1);
    const decisions = review?.output_type === "general_review" ? review.output?.decisions ?? [] : [];
    const reviewSources = review?.output_type === "general_review" ? review.output?.sources ?? {} : {};
    return (candidate.units ?? []).filter((unit) => unit.citation_status === "present").map((unit) => {
      const decision = decisions.find((item) => item.unit_id === unit.unit_id);
      return {
        nodeId: latest.node_execution_id, taskId: latest.task_id!,
        claim: {claim_key: unit.unit_id, text: unit.text, source_type: "file", kind: "assertion", scope: "read_blocks",
          evidence_refs: unit.source_aliases.map((alias) => candidate.sources[alias])},
        sourceKind: evidenceSourceKind(
          unit.source_aliases.map((alias) => candidate.sources[alias]),
        ),
        candidateStartChar: unit.start_char,
        candidateEndChar: unit.end_char,
        candidateKind: unit.kind,
        candidateSourceAliases: unit.source_aliases,
        assessment: decision ? {
          task_id: latest.task_id!, claim_key: unit.unit_id, verdict: decision.verdict,
          explanation: decision.feedback, evidence_refs: decision.source_aliases.map((alias) => reviewSources[alias]),
        } : undefined,
      };
    });
  }
  const assessments = new Map<string, ClaimAssessment>();
  for (const node of [...nodes].sort((a, b) => a.node_sequence - b.node_sequence)) {
    if (node.output_type === "independent_review" && node.status === "completed") {
      for (const item of node.output?.claim_assessments ?? []) {
        assessments.set(`${item.task_id}:${item.claim_key}`, item);
      }
    }
  }
  return nodes.flatMap((node) => {
    if (node.output_type !== "agent_model_execution" || node.status !== "completed" || !node.task_id || node.output?.structured_output.schema_version !== "worker_evidence.v2") return [];
    return (node.output?.structured_output.claims ?? []).map((claim) => ({
      nodeId: node.node_execution_id, taskId: node.task_id!, claim,
      sourceKind: evidenceSourceKind(claim.evidence_refs),
      assessment: assessments.get(`${node.task_id}:${claim.claim_key}`),
    }));
  });
}

/** Label a source projection by its actual evidence type, never by the Worker schema name. */
export function claimSourceKindLabel(sourceKind: ClaimEntry["sourceKind"]): string {
  if (sourceKind === "web") return "网页依据";
  if (sourceKind === "mixed") return "文件与网页依据";
  if (sourceKind === "file") return "文件结论";
  if (sourceKind === "none") return "一般判断";
  return "来源";
}

/** Choose an honest action label based on the persisted evidence kind. */
export function claimEvidenceActionLabel(sourceKind: ClaimEntry["sourceKind"]): string {
  if (sourceKind === "web") return "查看网页依据";
  if (sourceKind === "mixed") return "查看来源";
  if (sourceKind === "file") return "查看原文";
  return "查看依据";
}

/** Return a browser-navigable URL only for plain HTTP(S) source records. */
export function safeWebSourceUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

/** Prefer a persisted page title, then a safe hostname, without inventing a title. */
export function webSourceDisplayTitle(
  source: { filename: string; url?: string | null },
  block?: { title?: string | null; url?: string | null },
): string {
  const title = block?.title?.trim() || (
    source.filename !== "Web search results" ? source.filename.trim() : ""
  );
  if (title) return title;
  const safeUrl = safeWebSourceUrl(block?.url ?? source.url);
  if (safeUrl) return new URL(safeUrl).hostname;
  return "网页来源";
}

/** Locate citation tokens outside inline code within one candidate unit. */
function citationMatchesOutsideInlineCode(
  text: string,
): Array<{ alias: string; start: number; end: number }> {
  const matches: Array<{ alias: string; start: number; end: number }> = [];
  let index = 0;
  let codeDelimiterLength = 0;
  while (index < text.length) {
    if (text[index] === "`") {
      let length = 1;
      while (text[index + length] === "`") length += 1;
      if (codeDelimiterLength === 0) codeDelimiterLength = length;
      else if (length >= codeDelimiterLength) codeDelimiterLength = 0;
      index += length;
      continue;
    }
    if (codeDelimiterLength === 0 && text[index] === "[") {
      const match = /^\[(S[0-9]+)\]/.exec(text.slice(index));
      if (match) {
        matches.push({
          alias: match[1],
          start: index,
          end: index + match[0].length,
        });
        index += match[0].length;
        continue;
      }
    }
    index += 1;
  }
  return matches;
}

/** Convert a persisted Unicode code-point offset to a Markdown source string index. */
function codePointOffsetToStringIndex(text: string, targetOffset: number): number {
  let codePointOffset = 0;
  let stringIndex = 0;
  for (const character of text) {
    if (codePointOffset === targetOffset) return stringIndex;
    codePointOffset += 1;
    stringIndex += character.length;
  }
  return codePointOffset === targetOffset ? stringIndex : -1;
}

/**
 * Bind candidate citation tokens to persisted unit and source identities.
 * Candidate offsets count Unicode code points, while Markdown positions use JavaScript
 * UTF-16 indexes, so validate in code points and emit source indexes for the renderer.
 */
export function citationTargetsForContent(
  content: string,
  entries: readonly ClaimEntry[],
): ClaimCitationTarget[] {
  const contentCodePoints = Array.from(content);
  const targets: ClaimCitationTarget[] = [];
  for (const entry of entries) {
    if (
      entry.candidateKind === "code"
      || entry.candidateStartChar === undefined
      || entry.candidateEndChar === undefined
      || contentCodePoints
        .slice(entry.candidateStartChar, entry.candidateEndChar)
        .join("") !== entry.claim.text
    ) {
      continue;
    }
    const entryStart = codePointOffsetToStringIndex(content, entry.candidateStartChar);
    if (entryStart < 0) continue;
    for (const match of citationMatchesOutsideInlineCode(entry.claim.text)) {
      if (!entry.candidateSourceAliases?.includes(match.alias)) continue;
      const contentStart = entryStart + match.start;
      targets.push({
        key: `${entry.nodeId}:${entry.claim.claim_key}:${match.alias}:${contentStart}`,
        alias: match.alias,
        nodeId: entry.nodeId,
        claimKey: entry.claim.claim_key,
        contentStart,
        contentEnd: entryStart + match.end,
      });
    }
  }
  return targets.sort((left, right) => left.contentStart - right.contentStart);
}

/** Describe only parser-supplied locations, never manufacture document page numbers. */
export function sourceBlockLocation(block: ClaimSources["sources"][number]["blocks"][number]): string {
  return [
    block.page_number && `第 ${block.page_number} 页`,
    block.slide_number && `第 ${block.slide_number} 张幻灯片`,
    block.paragraph_number && `第 ${block.paragraph_number} 段`,
    block.sheet_name, block.cell_range,
    block.table_number && `表 ${block.table_number}`,
    block.row_number && `第 ${block.row_number} 行`,
    block.line_start && `第 ${block.line_start}${block.line_end && block.line_end !== block.line_start ? `–${block.line_end}` : ""} 行`,
  ].filter(Boolean).join(" · ") || "引用片段";
}

/** Label semantic review separately from source integrity and general-knowledge evaluation. */
export function claimVerdictLabel(entry: ClaimEntry): string {
  if (!entry.assessment) return "尚未审核";
  if (entry.assessment.verdict === "revise") return "审核有异议";
  if (entry.assessment.verdict === "unconfirmed") return "证据不足";
  if (entry.assessment.verdict === "contradicted") return entry.claim.source_type === "file" ? "与原文矛盾" : "审核有异议";
  if (entry.assessment.verdict === "insufficient_evidence") return "证据不足";
  return entry.claim.source_type === "file" ? "原文支持" : "审核认可";
}


/** Drop parser rows that contain neither text nor cells before rendering source excerpts. */
export function visibleClaimBlocks(
  blocks: ClaimSources["sources"][number]["blocks"],
): ClaimSources["sources"][number]["blocks"] {
  return blocks.filter((block) => Boolean(block.cells?.length) || Boolean(block.text?.trim()));
}
