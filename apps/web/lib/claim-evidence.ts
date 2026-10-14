import type { AgentWorkflowNodeResult, ClaimAssessment, ClaimSources, WorkerClaim } from "./types";

export type ClaimEntry = {
  nodeId: string;
  taskId: string;
  claim: WorkerClaim;
  assessment?: ClaimAssessment;
};

/** Associate committed claims with assessments by task and claim, never by list position. */
export function selectClaimEntries(nodes: AgentWorkflowNodeResult[]): ClaimEntry[] {
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
  if (entry.assessment.verdict === "contradicted") return entry.claim.source_type === "file" ? "与原文矛盾" : "审核有异议";
  if (entry.assessment.verdict === "insufficient_evidence") return "证据不足";
  return entry.claim.source_type === "file" ? "原文支持" : "审核认可";
}
