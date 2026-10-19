"""Project authorized original excerpts for a persisted claim without exposing private records."""

from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceNotFoundError
from nexuspilot_api.models import (
    AgentWorkflowNodeStatus,
    LlmAgentWorkflowNodeExecution,
    LlmModelAttempt,
    LlmModelToolCall,
)
from nexuspilot_api.schemas.worker_evidence import FileEvidenceReference, WorkerClaim
from nexuspilot_api.services.worker_evidence_service import (
    WorkerEvidenceService,
    allowed_evidence_tasks,
)


class SourceBlockRead(BaseModel):
    """Expose text and actual parser locations, excluding private storage metadata."""

    text: str
    line_start: int | None = None
    line_end: int | None = None
    paragraph_number: int | None = None
    page_number: int | None = None
    table_number: int | None = None
    row_number: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None
    slide_number: int | None = None
    cells: list[str] | None = None


class ClaimSourceRead(BaseModel):
    """Describe one validated read scope; this does not certify semantic correctness."""

    filename: str
    read_by: Literal["worker", "reviewer"]
    blocks: list[SourceBlockRead]
    has_more: bool | None
    total_blocks: int
    extraction_status: str
    warnings: list[str]
    source_valid: Literal[True] = True


class ClaimSourcesRead(BaseModel):
    """Return only the requested claim's authorized Worker and reviewer excerpts."""

    sources: list[ClaimSourceRead]


async def read_claim_sources(db, storage, workflow_id: str, node_id: str, claim_key: str):
    """Resolve legacy claims or located candidate units; reauthorize each original on every read."""
    node = await db.get(LlmAgentWorkflowNodeExecution, node_id)
    if (
        node is None
        or node.workflow_execution_id != workflow_id
        or node.output_type not in {"agent_model_execution", "general_candidate"}
        or node.status != AgentWorkflowNodeStatus.COMPLETED
    ):
        raise ResourceNotFoundError("Claim")
    if node.output_type == "general_candidate":
        output = node.output_json or {}
        unit = next((u for u in output.get("units", []) if u["unit_id"] == claim_key), None)
        if (
            output.get("schema_version") != "general_candidate.v2"
            or not unit
            or unit["citation_status"] != "present"
        ):
            raise ResourceNotFoundError("Claim")
        refs = [
            FileEvidenceReference.model_validate(output["sources"][alias])
            for alias in unit["source_aliases"]
        ]
    else:
        output = (node.output_json or {}).get("structured_output") or {}
        raw = next((c for c in output.get("claims", []) if c.get("claim_key") == claim_key), None)
        if output.get("schema_version") != "worker_evidence.v2" or raw is None:
            raise ResourceNotFoundError("Claim")
        refs = WorkerClaim.model_validate(raw).evidence_refs
    resolver = WorkerEvidenceService(db, storage)
    references = [("worker", node, ref) for ref in refs]
    reviewers = await db.scalars(
        select(LlmAgentWorkflowNodeExecution)
        .where(
            LlmAgentWorkflowNodeExecution.workflow_execution_id == workflow_id,
            LlmAgentWorkflowNodeExecution.output_type.in_({"independent_review", "general_review"}),
            LlmAgentWorkflowNodeExecution.status == AgentWorkflowNodeStatus.COMPLETED,
        )
        .order_by(LlmAgentWorkflowNodeExecution.node_sequence)
    )
    for reviewer in reviewers:
        if reviewer.output_type == "general_review":
            review = reviewer.output_json or {}
            if review.get("candidate_node_id") != node.node_execution_id:
                continue
            for decision in review.get("decisions", []):
                if decision["unit_id"] == claim_key:
                    for alias in decision["source_aliases"]:
                        references.append(
                            (
                                "reviewer",
                                reviewer,
                                FileEvidenceReference.model_validate(review["sources"][alias]),
                            )
                        )
            continue
        for assessment in (reviewer.output_json or {}).get("claim_assessments", []):
            if (
                assessment.get("task_id") == node.task_id
                and assessment.get("claim_key") == claim_key
            ):
                for value in assessment.get("evidence_refs", []):
                    ref = FileEvidenceReference.model_validate(value)
                    references.append(("reviewer", reviewer, ref))
    sources = []
    seen = set()
    for role, owner, ref in references:
        identity = (ref.tool_call_id, tuple(ref.block_ids))
        if identity in seen:
            continue
        seen.add(identity)
        source = await resolver.resolve(node.run_id, await allowed_evidence_tasks(db, owner), ref)
        # A revised candidate can cite a previous reviewer's read. Attribute the real
        # call's role, not the role of the later node which happens to cite it.
        record = await db.get(LlmModelToolCall, ref.tool_call_id)
        attempt = await db.get(LlmModelAttempt, record.attempt_id)
        role = "reviewer" if attempt.agent_role == "reviewer" else "worker"
        sources.append(
            ClaimSourceRead(
                filename=source["filename"],
                read_by=role,
                blocks=[SourceBlockRead.model_validate(block) for block in source["blocks"]],
                has_more=source["read_scope"]["has_more"],
                total_blocks=source["read_scope"]["parsed_document_blocks"],
                extraction_status=source["extraction_status"],
                warnings=source["warnings"],
            )
        )
    return ClaimSourcesRead(sources=sources)
