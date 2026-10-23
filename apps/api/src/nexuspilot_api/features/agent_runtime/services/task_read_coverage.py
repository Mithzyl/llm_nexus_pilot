"""Compute reading progress from committed tools, not model-authored notes or search scope."""

from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError, ResourceNotFoundError
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall, LlmRun, UserFile
from nexuspilot_api.services.conversation_file_service import conversation_file_ids
from nexuspilot_api.services.tool_context import FILE_BODY_TOOLS
from nexuspilot_api.services.user_file_service import read_parsed_file


def summarize_task_reads(document, file, results, *, offset, limit, planned_range=None):
    """Deduplicate exact returned blocks and page unread locations without copying source text.

    Results must come from successful tools in the authorized task. Check their
    snapshot and original blocks again; corrupt/obsolete history is not zero reads.
    A full-document search does not count its unreturned matches or search scope.
    """
    originals = {block.block_id: block.model_dump(mode="json") for block in document.blocks}
    returned = set()
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("file_id"), str):
            raise ResourceConflictError("Reading progress result is unavailable")
        if result.get("file_id") != file.file_id:
            continue
        expected = {"source_hash": file.content_hash, "parsed_hash": file.parsed_hash,
                    "parser_version": document.parser_version}
        if any(result.get(key) != value for key, value in expected.items()):
            raise ResourceConflictError("Reading progress snapshot has changed")
        blocks = result.get("data", {}).get("blocks")
        if not isinstance(blocks, list):
            raise ResourceConflictError("Reading progress original blocks are unavailable")
        for block in blocks:
            if not isinstance(block, dict):
                raise ResourceConflictError("Reading progress block is invalid")
            identity = block.get("block_id")
            if identity not in originals or block != originals[identity]:
                raise ResourceConflictError("Reading progress contains an invalid original block")
            returned.add(identity)
    unread = [(index, block) for index, block in enumerate(document.blocks)
              if block.block_id not in returned]
    result = {
        "schema_version": "task_read_coverage.v1", "scope": "current_task_tool_results",
        "file_id": file.file_id, "source_hash": file.content_hash,
        "parsed_hash": file.parsed_hash, "parser_version": document.parser_version,
        "total_parsed_blocks": len(originals), "returned_block_count": len(returned),
        "unreturned_block_count": len(unread),
        "all_parsed_blocks_returned": bool(originals) and not unread,
        "model_understanding_verified": False,
        "model_input_coverage": "not_measured_by_this_tool",
        "extraction_status": document.status, "warnings": document.warnings,
        "uncovered_pages": document.uncovered_pages,
        "next_unreturned_blocks": [{
            "block_id": block.block_id, "source_offset": index,
            "read_arguments": {"file_id": file.file_id, "block_id": block.block_id,
                               "before": 0, "limit": 20},
        } for index, block in unread[offset:offset + limit]],
        "next_offset": offset + limit if offset + limit < len(unread) else None,
        "pagination_note": "offset pages the current unread-block list; reads can change it. "
        "Use read_arguments for exact source locations. "
        "Missing parsed text is not readable coverage.",
    }
    if planned_range is not None:
        start, end = planned_range
        if not 0 <= start < end <= len(document.blocks):
            raise ResourceConflictError("Reading plan range is invalid")
        selected = {b.block_id for b in document.blocks[start:end]}
        result["planned_range"] = {
            "target_block_count": len(selected), "returned_block_count": len(selected & returned),
            "unreturned_block_count": len(selected - returned),
        }
    return result


async def task_read_coverage(db, storage, *, run_id, task_id, arguments, planned_range=None):
    """Inspect committed reads for the task, optionally counting a fixed parsed-block range."""
    run = await db.get(LlmRun, run_id, populate_existing=True)
    allowed = await conversation_file_ids(db, run_id)
    file = await db.get(UserFile, arguments.file_id, populate_existing=True)
    if (run is None or file is None or file.file_id not in allowed
            or file.user_id != run.user_id or file.status != "ready"):
        raise ResourceNotFoundError("File")
    document = await read_parsed_file(storage, file)
    records = await db.scalars(select(LlmModelToolCall).join(LlmModelAttempt).where(
        LlmModelAttempt.run_id == run_id, LlmModelAttempt.task_id == task_id,
        LlmModelToolCall.status == "completed", LlmModelToolCall.tool_name.in_(FILE_BODY_TOOLS),
    ).execution_options(populate_existing=True))
    return summarize_task_reads(
        document, file, ((record.output_json or {}).get("result") or {} for record in records),
        offset=arguments.offset, limit=arguments.limit,
        planned_range=planned_range,
    )
