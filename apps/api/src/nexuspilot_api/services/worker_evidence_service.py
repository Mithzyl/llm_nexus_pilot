"""Resolve model citations against authorized persisted reads and immutable parse objects."""

from sqlalchemy import select

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.models import (
    AgentWorkflowNodeStatus,
    LlmAgentWorkflowNodeExecution,
    LlmModelAttempt,
    LlmModelToolCall,
    UserFile,
)
from nexuspilot_api.schemas.worker_evidence import FileEvidenceReference, WorkerEvidenceBundle
from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService
from nexuspilot_api.services.user_file_service import read_parsed_file


async def allowed_evidence_tasks(db, node) -> set[str]:
    """Grant only this task and committed ancestors in the same persisted workflow graph."""
    tasks = {node.task_id} if node.task_id else set()
    pending = list((node.input_json or {}).get("source_node_execution_ids", []))
    seen = set()
    while pending:
        identity = pending.pop()
        if identity in seen:
            continue
        seen.add(identity)
        source = await db.get(LlmAgentWorkflowNodeExecution, identity, populate_existing=True)
        if (
            source is None
            or source.workflow_execution_id != node.workflow_execution_id
            or source.run_id != node.run_id
            or source.status != AgentWorkflowNodeStatus.COMPLETED
        ):
            raise InvalidRequestError("Evidence dependency is unavailable")
        if source.task_id:
            tasks.add(source.task_id)
        pending.extend((source.input_json or {}).get("source_node_execution_ids", []))
    return tasks


class WorkerEvidenceService:
    """Read original blocks without trusting model quotes, self-reported coverage or IDs."""

    def __init__(self, db, storage):
        """Use the existing SQL and object-store clients without duplicating stored results."""
        self.db = db
        self.storage = storage

    async def resolve(
        self, run_id: str, task_ids: set[str], reference: FileEvidenceReference
    ) -> dict:
        """Reauthorize a citation and return only its actually read blocks and platform scope."""
        record = await self.db.get(LlmModelToolCall, reference.tool_call_id, populate_existing=True)
        attempt = await self.db.get(LlmModelAttempt, record.attempt_id) if record else None
        if (
            record is None
            or attempt is None
            or attempt.run_id != run_id
            or attempt.task_id not in task_ids
            or record.status != "completed"
            or record.tool_name
            not in {"read_file_content", "read_file_table", "search_file_content"}
            or reference.result_id != f"tool-result:{record.tool_call_id}"
        ):
            raise InvalidRequestError("Evidence reference is unavailable")
        await AgentFileToolService(self.db, self.storage).validate_history(run_id, [record])
        result = (record.output_json or {}).get("result") or {}
        for key in ("file_id", "source_hash", "parsed_hash", "parser_version"):
            if result.get(key) != getattr(reference, key):
                raise InvalidRequestError("Evidence snapshot does not match the recorded read")
        file = await self.db.get(UserFile, reference.file_id, populate_existing=True)
        document = await read_parsed_file(self.storage, file)
        if document.parser_version != reference.parser_version:
            raise InvalidRequestError("Evidence parser version changed")
        returned = {block["block_id"]: block for block in result.get("data", {}).get("blocks", [])}
        originals = {block.block_id: block.model_dump(mode="json") for block in document.blocks}
        if len(set(reference.block_ids)) != len(reference.block_ids):
            raise InvalidRequestError("Evidence contains duplicate block identifiers")
        blocks = []
        for identity in reference.block_ids:
            if (
                identity not in returned
                or identity not in originals
                or returned[identity] != originals[identity]
            ):
                raise InvalidRequestError(
                    "Evidence block was not read or differs from the snapshot"
                )
            blocks.append(originals[identity])
        return {
            "reference": reference.model_dump(mode="json"),
            "filename": file.filename,
            "blocks": blocks,
            "read_scope": result["read_scope"],
            "warnings": list(document.warnings),
            "extraction_status": document.status,
            "all_parsed_block_ids": list(originals),
            "content_verified": False,
        }

    async def resolve_bundle(self, run_id, task_ids, bundle: WorkerEvidenceBundle) -> dict:
        """Resolve each claim and unresolved item; parsed-document scope requires all blocks."""
        claims = []
        for claim in bundle.claims:
            sources = [await self.resolve(run_id, task_ids, ref) for ref in claim.evidence_refs]
            if claim.scope == "parsed_document":
                files = {source["reference"]["file_id"] for source in sources}
                if not files:
                    raise InvalidRequestError("Document-wide claims require read evidence")
                for file_id in files:
                    selected = [s for s in sources if s["reference"]["file_id"] == file_id]
                    read_ids = {b["block_id"] for s in selected for b in s["blocks"]}
                    if read_ids != set(selected[0]["all_parsed_block_ids"]):
                        raise InvalidRequestError(
                            "Document-wide claim exceeds the cited read range"
                        )
            claims.append({**claim.model_dump(mode="json"), "sources": sources})
        unresolved = []
        for item in bundle.unresolved_items:
            sources = [await self.resolve(run_id, task_ids, ref) for ref in item.evidence_refs]
            unresolved.append({**item.model_dump(mode="json"), "sources": sources})
        return {"claims": claims, "unresolved_items": unresolved, "hypotheses": bundle.hypotheses}

    async def for_node(self, node) -> list[dict]:
        """Reauthorize ancestor excerpts before every model round, including repairs."""
        tasks = await allowed_evidence_tasks(self.db, node)
        sources = list(
            await self.db.scalars(
                select(LlmAgentWorkflowNodeExecution).where(
                    LlmAgentWorkflowNodeExecution.workflow_execution_id
                    == node.workflow_execution_id,
                    LlmAgentWorkflowNodeExecution.task_id.in_(tasks - {node.task_id}),
                    LlmAgentWorkflowNodeExecution.output_type == "agent_model_execution",
                    LlmAgentWorkflowNodeExecution.status == AgentWorkflowNodeStatus.COMPLETED,
                )
            )
        )
        results = []
        for source in sources:
            output = (source.output_json or {}).get("structured_output") or {}
            if output.get("schema_version") != "worker_evidence.v2":
                continue
            bundle = WorkerEvidenceBundle.model_validate(
                {key: output[key] for key in ("claims", "unresolved_items", "hypotheses")}
            )
            results.append(
                {"task_id": source.task_id, **await self.resolve_bundle(node.run_id, tasks, bundle)}
            )
        return results
