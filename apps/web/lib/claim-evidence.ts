import type { AgentWorkflowNodeResult, ClaimAssessment, ClaimSources, WorkerClaim } from "./types";

export type ClaimEntry = {
  nodeId: string;
  taskId: string;
  claim: WorkerClaim;
  assessment?: Omit<ClaimAssessment, "verdict"> & {
    verdict: ClaimAssessment["verdict"] | "revise" | "unconfirmed";
  };
};

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
      assessment: assessments.get(`${node.task_id}:${claim.claim_key}`),
    }));
  });
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
