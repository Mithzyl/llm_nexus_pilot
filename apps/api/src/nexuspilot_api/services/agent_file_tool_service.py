"""Execute fixed read-only file tools against this Run's owned conversation attachments.

No arbitrary paths, imports, commands, network targets or runtime packages are accepted.
Each call is committed before reading storage and its exact result before model continuation.
"""

import asyncio
import json
from typing import Literal

from nexuspilot_models.contracts import ToolCall, ToolDefinition
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nexuspilot_api.core.errors import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from nexuspilot_api.infrastructure.object_storage import ObjectStorage
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall, LlmRun, UserFile
from nexuspilot_api.models.base import utc_now
from nexuspilot_api.services.conversation_file_service import conversation_file_ids
from nexuspilot_api.services.file_parsing_service import (
    get_file_outline,
    read_file_content,
    read_file_table,
    search_file_content,
)
from nexuspilot_api.services.tool_catalog import RegisteredTool, ToolCatalog
from nexuspilot_api.services.tool_context import bound_file_page
from nexuspilot_api.services.user_file_service import read_parsed_file


class EmptyArguments(BaseModel):
    """Reject all model-authored identity, permission or filesystem arguments."""

    model_config = ConfigDict(extra="forbid", strict=True)


class FileArguments(EmptyArguments):
    """Identify a private uploaded file; ownership always comes from the persisted Run."""

    file_id: str = Field(min_length=36, max_length=36)


class PageArguments(FileArguments):
    """Page extracted blocks without confusing source pages with zero-based block offsets."""

    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=50, ge=1, le=100)


class SearchArguments(PageArguments):
    """Search literal text, never regular expressions or executable queries."""

    query: str = Field(min_length=1, max_length=256)


class ContentArguments(PageArguments):
    """Read by source offset or directly around an exact original block ID."""

    block_id: str | None = Field(default=None, min_length=1, max_length=256)
    before: int = Field(default=5, ge=0, le=20)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_location(self):
        """Reject contradictory anchors and offsets rather than silently choosing one."""
        if self.block_id is not None and self.offset != 0:
            raise ValueError("Use block_id or offset, not both")
        if self.block_id is not None and self.before >= self.limit:
            raise ValueError("limit must include the anchor after before blocks")
        return self


def content_start_offset(document, arguments: ContentArguments) -> int:
    """Resolve a validated anchor in the authorized immutable document, preserving source order."""
    if arguments.block_id is None:
        return arguments.offset
    for index, block in enumerate(document.blocks):
        if block.block_id == arguments.block_id:
            return max(0, index - arguments.before)
    raise ValueError("Unknown block_id in this file snapshot")


def search_read_targets(document, page, file_id: str) -> list[dict]:
    """Return exact source positions and bounded reread arguments only for delivered matches."""
    positions = {block.block_id: index for index, block in enumerate(document.blocks)}
    return [{"block_id": block.block_id, "source_offset": positions[block.block_id],
             "read_arguments": {"file_id": file_id, "block_id": block.block_id,
                                "before": 5, "limit": 20}}
            for block in page.blocks]


class FileToolResult(BaseModel):
    """Separate empty successful reads from denials, execution errors and cancellation."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "permission_denied", "failed", "timed_out", "cancelled"]
    result: dict | None = None
    error_code: str | None = None


class CatalogSearchArguments(EmptyArguments):
    """Bound literal catalog searches without exposing owner, scope or execution policy inputs."""

    query: str = Field(default="", max_length=256)
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=10, ge=1, le=20)


class DescribeToolArguments(EmptyArguments):
    """Select one exact version to describe and load for the next model request."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


def register_file_tool(name, description, arguments, *, keywords=(), initially_loaded=False):
    """Create trusted read-tool metadata with the same result envelope used in persisted calls."""
    return RegisteredTool(
        name=name,
        version="1.0.0",
        description=description,
        arguments=arguments,
        output=FileToolResult,
        required_capabilities=frozenset({"read"}),
        keywords=keywords,
        initially_loaded=initially_loaded,
    )


FILE_TOOLS = tuple(
    [
        register_file_tool(
            "list_run_files",
            "List files attached at this conversation turn.",
            EmptyArguments,
            keywords=("附件", "文件", "列表"),
            initially_loaded=True,
        ),
        register_file_tool(
            "get_file_outline",
            "Read extracted headings and coverage before choosing document ranges; "
            "not a complete visual outline.",
            FileArguments,
            keywords=("结构", "目录", "章节", "outline"),
        ),
        register_file_tool(
            "read_file_content",
            "Read saved text from uploaded text, Word/DOCX, Excel/XLSX, PowerPoint/PPTX or PDF. "
            "For a search hit, use its block_id with before=5 limit=20 to read nearby text "
            "directly; do NOT scan preceding pages. Otherwise use zero-based BLOCK offset "
            "(not page number) and limit; next_offset is optional continuation, NOT an instruction "
            "to read the whole file. Follow it only when the question needs that next range; "
            "which may advance less than limit to keep results small. "
            "For specific questions search_file_content first instead of reading every block. "
            "Reads cached extraction, not images or arbitrary local paths.",
            ContentArguments,
            keywords=("正文", "文字", "读取", "Office", "文档", "电子表格", "幻灯片"),
            initially_loaded=True,
        ),
        register_file_tool(
            "read_file_table",
            "Read saved table rows from Excel/XLSX, Word/DOCX, PowerPoint/PPTX or CSV/TSV; "
            "formulas are not executed. PDF structured tables are not supported.",
            PageArguments,
            keywords=("表格", "Excel", "CSV", "行"),
        ),
        register_file_tool(
            "search_file_content",
            "Search ALL extracted blocks, including unread document end, for literal text. "
            "Offset paginates MATCHES, not source pages. Try alternative keywords if no match; "
            "Use returned read_targets read_arguments with read_file_content for nearby text. "
            "no match does not prove absence from images or unextracted content.",
            SearchArguments,
            keywords=("搜索", "查找", "文字"),
            initially_loaded=True,
        ),
    ]
)

FILE_TOOL_CATALOG = ToolCatalog(
    (
        *FILE_TOOLS,
        register_file_tool(
            "search_tools",
            "Search permitted built-in tools by purpose; searching does not load them.",
            CatalogSearchArguments,
            keywords=("工具", "搜索"),
            initially_loaded=True,
        ),
        register_file_tool(
            "describe_tools",
            "Describe one exact tool version and load its schema NEXT model round. "
            "This does not grant access to files or allow commands.",
            DescribeToolArguments,
            keywords=("工具", "说明", "加载"),
            initially_loaded=True,
        ),
    )
)


class AgentFileToolService:
    """Authorize and record tool reads with no independently callable execution endpoint."""

    def __init__(self, db: AsyncSession, storage: ObjectStorage):
        """Bind one Worker's read-only policy and initially loaded tools to existing storage."""
        self.db = db
        self.storage = storage
        self.capabilities = frozenset({"read"})
        self.catalog = FILE_TOOL_CATALOG
        self.loaded_versions = {
            entry.name: entry.version for entry in self.catalog.entries if entry.initially_loaded
        }

    def definitions(self) -> list[ToolDefinition]:
        """Resolve loaded versions against current policy before each Provider request."""
        return [
            entry.definition()
            for name, version in self.loaded_versions.items()
            if (entry := self.catalog.get(name, version, self.capabilities)) is not None
        ]

    async def execute(
        self,
        *,
        attempt_id: str,
        call: ToolCall,
        offered_versions: dict[str, str] | None = None,
    ) -> LlmModelToolCall:
        """Claim once and check policy before dispatching a pinned registered operation.

        File operations remain read-only; task writes use trusted catalog bounds.
        A delegated model task inherits its enclosing workflow deadline instead
        of the short file-read timeout. Both retain durable results and cancellation.
        """
        if len(call.id) > 255:
            raise ResourceConflictError("Provider tool call identifier exceeds supported length")
        attempt = await self.db.get(LlmModelAttempt, attempt_id)
        if attempt is None:
            raise ResourceNotFoundError("Model attempt")
        existing = await self.db.scalar(
            select(LlmModelToolCall).where(
                LlmModelToolCall.attempt_id == attempt_id,
                LlmModelToolCall.provider_tool_call_id == call.id,
            )
        )
        if existing is not None:
            # Refuse replay rather than leaking cached content after deletion or revocation.
            raise ResourceConflictError(
                "Tool call already recorded; execution will not be repeated"
            )
        offered_versions = dict(
            self.loaded_versions if offered_versions is None else offered_versions
        )
        tool = next(
            (
                entry
                for entry in self.catalog.entries
                if entry.name == call.name and entry.required_capabilities <= self.capabilities
            ),
            None,
        )
        record = LlmModelToolCall(
            attempt_id=attempt_id,
            provider_tool_call_id=call.id,
            tool_name=call.name,
            tool_version=tool.version if tool else None,
            risk_level=tool.risk_level if tool else "unclassified",
            input_json=call.arguments,
            status="requested",
            permission_decision=None,
        )
        self.db.add(record)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise ResourceConflictError("Tool call was concurrently recorded") from exc
        try:
            if tool is None:
                return await self._finish(record, "permission_denied", error="tool_not_allowed")
            if offered_versions.get(tool.name) != tool.version:
                return await self._finish(record, "permission_denied", error="tool_not_loaded")
            arguments = tool.arguments.model_validate(call.arguments)
            record.permission_decision = "allowed"
            record.status = "running"
            await self.db.commit()
            async with asyncio.timeout(tool.timeout_seconds):
                result = await self._execute_registered_tool(attempt, record, tool.name, arguments)
            if len(json.dumps(result, ensure_ascii=False).encode()) > tool.max_result_bytes:
                return await self._finish(
                    record, "failed", error="result_too_large_use_smaller_page"
                )
            finished = await self._finish(record, "completed", result=result)
            if tool.name == "describe_tools":
                self.loaded_versions[result["name"]] = result["version"]
            return finished
        except ValidationError:
            return await self._finish(record, "failed", error="invalid_arguments")
        except ToolOperationError as error:
            return await self._finish(record, error.status, error=error.code)
        except ToolUnavailableError:
            return await self._finish(record, "permission_denied", error="tool_unavailable")
        except (ResourceConflictError, ResourceNotFoundError):
            return await self._finish(record, "permission_denied", error="file_unavailable")
        except ValueError:
            return await self._finish(record, "failed", error="invalid_read_range")
        except TimeoutError:
            return await self._finish(record, "timed_out", error="tool_timeout")
        except asyncio.CancelledError:
            await self._finish(record, "cancelled", error="tool_cancelled")
            raise
        except Exception:
            await self.db.rollback()
            await self._finish(record, "failed", error="tool_execution_failed")
            raise

    async def _execute_registered_tool(self, attempt, record, name, arguments):
        """Dispatch default file operations; subclasses can use server-owned call/task IDs."""
        return await self._read(attempt.run_id, name, arguments)

    async def _read(self, run_id: str, name: str, arguments: EmptyArguments) -> dict:
        """Authorize the snapshot and return bounded reads or located search matches."""
        run = await self.db.get(LlmRun, run_id, populate_existing=True)
        if run is None:
            raise ResourceNotFoundError("Run")
        if name == "search_tools":
            assert isinstance(arguments, CatalogSearchArguments)
            return self.catalog.search(
                arguments.query,
                capabilities=self.capabilities,
                offset=arguments.offset,
                limit=arguments.limit,
            )
        if name == "describe_tools":
            assert isinstance(arguments, DescribeToolArguments)
            entry = self.catalog.get(arguments.name, arguments.version, self.capabilities)
            if entry is None:
                raise ToolUnavailableError
            return entry.describe()
        ids = await conversation_file_ids(self.db, run_id)
        if name == "list_run_files":
            files = list(
                await self.db.scalars(
                    select(UserFile)
                    .where(
                        UserFile.file_id.in_(ids),
                        UserFile.user_id == run.user_id,
                        UserFile.status == "ready",
                    )
                    .order_by(UserFile.file_id)
                )
            )
            return {
                "files": [
                    {"file_id": f.file_id, "filename": f.filename, "media_type": f.mime_type}
                    for f in files
                ]
            }
        assert isinstance(arguments, FileArguments)
        file = await self.db.get(UserFile, arguments.file_id, populate_existing=True)
        if (
            arguments.file_id not in ids
            or file is None
            or file.user_id != run.user_id
            or file.status != "ready"
        ):
            raise ResourceNotFoundError("File")
        document = await read_parsed_file(self.storage, file)
        source_offset = getattr(arguments, "offset", None)
        if name == "get_file_outline":
            data = get_file_outline(document)
        else:
            assert isinstance(arguments, PageArguments)
            if name == "search_file_content":
                assert isinstance(arguments, SearchArguments)
                page = search_file_content(
                    document, arguments.query, offset=arguments.offset, limit=arguments.limit
                )
            else:
                if name == "read_file_content":
                    source_offset = content_start_offset(document, arguments)
                reader = read_file_table if name == "read_file_table" else read_file_content
                page = reader(document, offset=source_offset, limit=arguments.limit)
            page = bound_file_page(page, offset=source_offset)
            if (isinstance(arguments, ContentArguments) and arguments.block_id is not None
                    and arguments.block_id not in {block.block_id for block in page.blocks}):
                # Large preceding blocks must not displace the requested anchor.
                source_offset = content_start_offset(
                    document, arguments.model_copy(update={"before": 0})
                )
                page = bound_file_page(
                    read_file_content(document, offset=source_offset, limit=arguments.limit),
                    offset=source_offset,
                )
            data = page.model_dump(mode="json")
            if name == "search_file_content":
                data["read_targets"] = search_read_targets(document, page, file.file_id)
            data["continuation_note"] = (
                "has_more describes remaining data, not required work. Answer once the requested "
                "facts are supported. Search and read around hits instead of scanning sequentially."
            )
        return {
            "file_id": file.file_id,
            "source_hash": file.content_hash,
            "parsed_hash": file.parsed_hash,
            "parser_version": document.parser_version,
            "status": document.status,
            "warnings": document.warnings,
            "data": data,
            "read_scope": {
                "kind": name,
                "offset": source_offset,
                "anchor_block_id": getattr(arguments, "block_id", None),
                "limit": getattr(arguments, "limit", None),
                "query": getattr(arguments, "query", None),
                "has_more": data.get("has_more"),
                "next_offset": data.get("next_offset"),
                "total_blocks": data.get("total_blocks"),
                "parsed_document_blocks": len(document.blocks),
                "extraction_status": document.status,
                "uncovered_pages": document.uncovered_pages,
            },
        }

    async def validate_history(self, run_id: str, records: list[LlmModelToolCall]) -> None:
        """Reject deleted, reassigned or changed files before sending previous tool data again."""
        run = await self.db.get(LlmRun, run_id, populate_existing=True)
        allowed = await conversation_file_ids(self.db, run_id)
        for record in records:
            if (
                self.catalog.get(record.tool_name, record.tool_version or "", self.capabilities)
                is None
            ):
                if record.status == "completed":
                    raise ResourceConflictError("Previously used tool is no longer available")
            result = (record.output_json or {}).get("result") or {}
            if record.tool_name == "describe_tools" and record.status == "completed":
                if self.catalog.get(result["name"], result["version"], self.capabilities) is None:
                    raise ResourceConflictError("Previously described tool is no longer available")
            if record.tool_name == "search_tools" and record.status == "completed":
                if any(
                    self.catalog.get(item["name"], item["version"], self.capabilities) is None
                    for item in result["items"]
                ):
                    raise ResourceConflictError("Previously discovered tool is no longer available")
            sources = result.get("files", []) if record.tool_name == "list_run_files" else [result]
            for source in sources:
                file_id = source.get("file_id")
                if not file_id:
                    continue
                file = await self.db.get(UserFile, file_id, populate_existing=True)
                if (
                    run is None
                    or file is None
                    or file_id not in allowed
                    or file.user_id != run.user_id
                    or file.status != "ready"
                    or source.get("source_hash", file.content_hash) != file.content_hash
                    or source.get("parsed_hash", file.parsed_hash) != file.parsed_hash
                ):
                    raise ResourceConflictError("Previously read file is no longer available")

    async def _finish(self, record, status, *, result=None, error=None):
        """Publish the result and citable snapshot identity before model continuation."""
        if status == "completed" and result and result.get("data", {}).get("blocks"):
            result["evidence_ref"] = {
                "tool_call_id": record.tool_call_id,
                "result_id": f"tool-result:{record.tool_call_id}",
                **{
                    key: result[key]
                    for key in ("file_id", "source_hash", "parsed_hash", "parser_version")
                },
                "block_ids": [block["block_id"] for block in result["data"]["blocks"]],
            }
        record.status = status
        record.error_code = error
        record.permission_decision = (
            "denied" if status == "permission_denied" else record.permission_decision
        )
        record.output_json = FileToolResult(
            status=status, result=result, error_code=error
        ).model_dump(mode="json")
        record.completed_at = utc_now()
        await self.db.commit()
        return record


def file_tool_guidance(*, final_format: str = "final JSON") -> str:
    """Derive planning guidance from the same catalog that supplies initial tool definitions."""
    loaded = sorted(entry.name for entry in FILE_TOOL_CATALOG.entries if entry.initially_loaded)
    return (
        "Already loaded tools: " + ", ".join(loaded) + ". Call these directly; "
        "do NOT search or describe them first. Only discover/describe tools not in this list. "
        f"Continue useful reads until evidence supports the task, then submit {final_format}. "
        "For pagination pass the returned next_offset as the next request's offset parameter. "
        "Search matches may be incomplete lines; read surrounding source before inferring meaning."
        " Search results contain read_targets: pass read_arguments directly to read_file_content. "
        "has_more/next_offset indicate available data, not a requirement to read all of it. "
        "Stop reading when each requested point has supporting text; submit the answer with "
        "citations and honest limitations. Do not scan unrelated sections to achieve full coverage."
    )


class ToolUnavailableError(Exception):
    """Represent hidden, absent or incompatible catalog entries without revealing which one."""


class ToolOperationError(Exception):
    """Carry a server-defined tool failure without revealing arbitrary exception text."""

    def __init__(self, code: str, *, status: str = "failed"):
        """Retain a safe diagnostic code and result state for the shared execution audit."""
        super().__init__(code)
        self.code = code
        self.status = status
