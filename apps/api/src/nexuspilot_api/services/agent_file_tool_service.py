"""Execute fixed read-only file tools against this Run's owned conversation attachments.

No arbitrary paths, imports, commands, network targets or runtime packages are accepted.
Each call is committed before reading storage and its exact result before model continuation.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import Literal

from nexuspilot_models.contracts import ToolCall, ToolDefinition
from pydantic import BaseModel, ConfigDict, Field, ValidationError
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


class FileToolResult(BaseModel):
    """Separate empty successful reads from denials, execution errors and cancellation."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["completed", "permission_denied", "failed", "timed_out", "cancelled"]
    result: dict | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class RegisteredFileTool:
    """Pin the only approved implementation and input schema for a built-in read tool."""

    name: str
    description: str
    arguments: type[EmptyArguments]
    version: str = "1.0.0"

    def definition(self) -> ToolDefinition:
        """Publish callable metadata without storage addresses or execution privileges."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.arguments.model_json_schema(),
        )


FILE_TOOLS = tuple(
    [
        RegisteredFileTool(
            "list_run_files", "List files attached at this conversation turn.", EmptyArguments
        ),
        RegisteredFileTool(
            "get_file_outline",
            "Read extracted headings and coverage; not a complete visual outline.",
            FileArguments,
        ),
        RegisteredFileTool(
            "read_file_content",
            "Read extracted blocks using zero-based offset and limit; follow next_offset.",
            PageArguments,
        ),
        RegisteredFileTool(
            "read_file_table",
            "Read extracted table rows; formulas are not executed.",
            PageArguments,
        ),
        RegisteredFileTool(
            "search_file_content",
            "Find literal text in extracted blocks; paginate matches with offset.",
            SearchArguments,
        ),
    ]
)


class AgentFileToolService:
    """Authorize and record tool reads with no independently callable execution endpoint."""

    def __init__(self, db: AsyncSession, storage: ObjectStorage):
        """Bind request-scoped persistence and the existing private object storage."""
        self.db = db
        self.storage = storage

    async def execute(self, *, attempt_id: str, call: ToolCall) -> LlmModelToolCall:
        """Claim a call once, record denial/error/success, and never retry reads implicitly."""
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
        tool = next((tool for tool in FILE_TOOLS if tool.name == call.name), None)
        record = LlmModelToolCall(
            attempt_id=attempt_id,
            provider_tool_call_id=call.id,
            tool_name=call.name,
            tool_version=tool.version if tool else None,
            risk_level="read_only" if tool else "unclassified",
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
            arguments = tool.arguments.model_validate(call.arguments)
            record.permission_decision = "allowed"
            record.status = "running"
            await self.db.commit()
            async with asyncio.timeout(15):
                result = await self._read(attempt.run_id, tool.name, arguments)
            if len(json.dumps(result, ensure_ascii=False).encode()) > 65536:
                return await self._finish(
                    record, "failed", error="result_too_large_use_smaller_page"
                )
            return await self._finish(record, "completed", result=result)
        except ValidationError:
            return await self._finish(record, "failed", error="invalid_arguments")
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

    async def _read(self, run_id: str, name: str, arguments: EmptyArguments) -> dict:
        """Recheck owner, conversation anchor and file readiness before every storage read."""
        run = await self.db.get(LlmRun, run_id, populate_existing=True)
        if run is None:
            raise ResourceNotFoundError("Run")
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
                reader = read_file_table if name == "read_file_table" else read_file_content
                page = reader(document, offset=arguments.offset, limit=arguments.limit)
            data = page.model_dump(mode="json")
        return {
            "file_id": file.file_id,
            "source_hash": file.content_hash,
            "parser_version": document.parser_version,
            "status": document.status,
            "warnings": document.warnings,
            "data": data,
        }

    async def validate_history(self, run_id: str, records: list[LlmModelToolCall]) -> None:
        """Reject deleted, reassigned or changed files before sending previous tool data again."""
        run = await self.db.get(LlmRun, run_id, populate_existing=True)
        allowed = await conversation_file_ids(self.db, run_id)
        for record in records:
            result = (record.output_json or {}).get("result") or {}
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
                ):
                    raise ResourceConflictError("Previously read file is no longer available")

    async def _finish(self, record, status, *, result=None, error=None):
        """Publish one exact, safe result envelope before the next model invocation."""
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
