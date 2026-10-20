"""Private, immutable task notes using existing audited Tool Calls and MinIO bodies.

These are model-authored working notes, not verified claims, shared Memory, or
restart checkpoints. Only the current task can read them. A save succeeds when
the shared executor commits its output; no separate object pointer or side write
can claim success before that commit.
"""

from typing import Annotated, Literal

from pydantic import Field
from sqlalchemy import select

from nexuspilot_api.core.errors import ResourceConflictError
from nexuspilot_api.features.agent_runtime.services.task_input_coverage import (
    authorize_input_coverage_history,
    task_input_coverage,
)
from nexuspilot_api.features.agent_runtime.services.task_input_snapshot import (
    load_task_input_snapshot_manifest,
    read_task_input_snapshot,
)
from nexuspilot_api.features.agent_runtime.services.task_read_coverage import task_read_coverage
from nexuspilot_api.models import LlmModelAttempt, LlmModelToolCall, LlmRun, LlmTask, TaskStatus
from nexuspilot_api.schemas.worker_evidence import FileEvidenceReference
from nexuspilot_api.services.agent_file_tool_service import (
    FILE_TOOL_CATALOG,
    AgentFileToolService,
    EmptyArguments,
    FileToolResult,
    PageArguments,
    ToolOperationError,
)
from nexuspilot_api.services.tool_catalog import RegisteredTool, ToolCatalog
from nexuspilot_api.services.worker_evidence_service import WorkerEvidenceService

ShortItem = Annotated[str, Field(min_length=1, max_length=300)]


class SaveTaskNoteArguments(EmptyArguments):
    """Bound provisional prose and short source names; forbid caller-controlled scope."""

    note: str = Field(min_length=1, max_length=6000)
    unresolved_items: list[ShortItem] = Field(default_factory=list, max_length=10)
    next_actions: list[ShortItem] = Field(default_factory=list, max_length=10)
    source_aliases: list[Annotated[str, Field(pattern=r"^S[0-9]+$")]] = Field(
        default_factory=list, max_length=20,
    )


class ReadTaskNoteArguments(EmptyArguments):
    """Identify an immutable note; the server always supplies its owning task."""

    note_id: str = Field(min_length=36, max_length=36)


class DelegateTaskArguments(EmptyArguments):
    """Request a bounded task objective, not a new identity, provider or permission grant."""

    role: Literal["planner", "researcher"]
    objective: str = Field(min_length=1, max_length=4000)
    source_aliases: list[Annotated[str, Field(pattern=r"^S[0-9]+$")]] = Field(
        default_factory=list, max_length=20,
    )


class ListTaskNotesArguments(EmptyArguments):
    """Page note metadata for this task without injecting every note into model input."""

    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=10, ge=1, le=20)


class ReadTaskInputSnapshotArguments(ListTaskNotesArguments):
    """Page a server-granted immutable input snapshot, without accepting arbitrary task IDs."""

    snapshot_id: str = Field(min_length=36, max_length=36)


class TaskNote(EmptyArguments):
    """Store immutable provisional text and server-resolved references, never a truth label."""

    schema_version: Literal["task_note.v1"] = "task_note.v1"
    note_id: str
    note: str
    unresolved_items: list[str]
    next_actions: list[str]
    sources: dict[str, FileEvidenceReference]
    content_verified: Literal[False] = False


NOTE_TOOLS = (
    RegisteredTool(
        name="save_task_note", version="1.0.0",
        description="Save a private immutable note: progress, unresolved items and next actions. "
        "Use short source_aliases from actual reads. This is provisional, not verified evidence "
        "or a final answer. Save corrections as a new note; original notes are never overwritten.",
        arguments=SaveTaskNoteArguments, output=FileToolResult,
        required_capabilities=frozenset({"task_notes"}), initially_loaded=True,
        keywords=("笔记", "计划", "进度", "notes", "plan"), risk_level="task_write",
    ),
    RegisteredTool(
        name="read_task_note", version="1.0.0",
        description="Read a saved note_id from this task only; sources are reauthorized. "
        "Notes are provisional and cannot replace rereading original file evidence.",
        arguments=ReadTaskNoteArguments, output=FileToolResult,
        required_capabilities=frozenset({"task_notes"}), initially_loaded=True,
        keywords=("笔记", "notes", "read"),
    ),
    RegisteredTool(
        name="list_task_notes", version="1.0.0",
        description="List immutable note IDs and creation times in this task, oldest first. "
        "Read a selected note using read_task_note; other roles' notes are not shared.",
        arguments=ListTaskNotesArguments, output=FileToolResult,
        required_capabilities=frozenset({"task_notes"}),
        keywords=("笔记", "notes", "list"),
    ),
)
NOTE_NAMES = {tool.name for tool in NOTE_TOOLS}
READ_COVERAGE_TOOL = RegisteredTool(
    name="get_task_read_coverage", version="1.0.0",
    description="Inspect this task's actual returned blocks and remaining unread locations for "
    "one attached file. offset/limit page the unread list, NOT source blocks. This does not read "
    "source text, prove model understanding, or count a full-document search as full reading. "
    "Use returned read_arguments for targeted continuation when your objective needs more reading.",
    arguments=PageArguments, output=FileToolResult,
    required_capabilities=frozenset({"task_notes", "read"}),
    keywords=("阅读", "未读", "进度", "coverage", "reading", "progress"),
)
INPUT_COVERAGE_TOOL = RegisteredTool(
    name="get_task_input_coverage", version="1.0.0",
    description="Page this task's prepared model requests and per-file input counts/hashes. "
    "This is not tool-read coverage, provider receipt or understanding. Historical missing "
    "audits remain unknown. No source text is returned; deduplicate attempt IDs across pages.",
    arguments=ListTaskNotesArguments, output=FileToolResult,
    required_capabilities=frozenset({"task_notes", "read"}),
    keywords=("输入", "上下文", "input", "coverage", "context"),
)
INPUT_SNAPSHOT_TOOL = RegisteredTool(
    name="read_task_input_snapshot", version="1.0.0",
    description="Read a fixed candidate task input page using an explicitly supplied snapshot_id. "
    "The page identifies its task and request set; later requests do not alter it. "
    "Use this to check another task's input claims. Your own live task coverage cannot "
    "disprove records from the candidate task. No source prose or private reasoning is returned.",
    arguments=ReadTaskInputSnapshotArguments, output=FileToolResult,
    required_capabilities=frozenset({"task_notes", "read"}),
    keywords=("输入", "审核", "snapshot", "input", "review", "coverage"),
)
DELEGATE_TOOL = RegisteredTool(
    name="delegate_task", version="1.0.0",
    description="Delegate a focused task to a configured planner or researcher and receive its "
    "full prose result and source references. Choose source_aliases to supply original excerpts. "
    "The child can read authorized attachments but cannot delegate again "
    "or approve the final answer. "
    "Use only when a separate analysis is useful; ordinary steps need no delegation.",
    arguments=DelegateTaskArguments, output=FileToolResult,
    required_capabilities=frozenset({"delegate"}), initially_loaded=True,
    keywords=("委派", "planner", "researcher", "delegate"), risk_level="task_write",
    timeout_seconds=None, max_result_bytes=2 * 1024 * 1024,
)
GENERAL_TOOL_CATALOG = ToolCatalog(
    (*FILE_TOOL_CATALOG.entries, *NOTE_TOOLS,
     READ_COVERAGE_TOOL, INPUT_COVERAGE_TOOL, INPUT_SNAPSHOT_TOOL, DELEGATE_TOOL)
)


class GeneralAgentToolService(AgentFileToolService):
    """Add task notes, read/input audits and delegation without enabling host writes."""

    def __init__(
        self, db, storage, *, task_id, sources, allowed_task_ids,
        delegate=None, input_snapshots=None,
    ):
        """Bind server-owned task and source mappings; models cannot expand this scope."""
        super().__init__(db, storage)
        self.task_id = task_id
        self.sources = sources
        self.allowed_task_ids = allowed_task_ids
        self.delegate = delegate
        self.input_snapshots = input_snapshots if input_snapshots is not None else {}
        self.catalog = GENERAL_TOOL_CATALOG
        self.capabilities = frozenset(
            {"read", "task_notes"} | ({"delegate"} if delegate else set())
        )
        self.loaded_versions = {
            tool.name: tool.version for tool in self.catalog.entries if tool.initially_loaded
        }
        if self.input_snapshots:
            self.loaded_versions[INPUT_SNAPSHOT_TOOL.name] = INPUT_SNAPSHOT_TOOL.version

    async def _execute_registered_tool(self, attempt, record, name, arguments):
        """Authorize task notes, read/input queries and delegation before shared persistence."""
        if name not in NOTE_NAMES | {
            READ_COVERAGE_TOOL.name, INPUT_COVERAGE_TOOL.name, INPUT_SNAPSHOT_TOOL.name,
            DELEGATE_TOOL.name,
        }:
            return await super()._execute_registered_tool(attempt, record, name, arguments)
        task = await self.db.get(LlmTask, self.task_id, populate_existing=True)
        run = await self.db.get(LlmRun, attempt.run_id, populate_existing=True)
        if (task is None or attempt.task_id != self.task_id or task.run_id != attempt.run_id
                or run is None or task.status != TaskStatus.RUNNING
                or run.status.value not in {"pending", "running"}):
            raise ToolOperationError("task_not_running", status="permission_denied")
        if name == DELEGATE_TOOL.name:
            if self.delegate is None:
                raise ToolOperationError("delegation_not_allowed", status="permission_denied")
            return await self.delegate(arguments)
        if name == READ_COVERAGE_TOOL.name:
            return await task_read_coverage(
                self.db, self.storage, run_id=attempt.run_id, task_id=self.task_id,
                arguments=arguments,
            )
        if name == INPUT_COVERAGE_TOOL.name:
            if task.assigned_role == "reviewer" and self.input_snapshots:
                raise ToolOperationError("review_requires_read_task_input_snapshot")
            return await task_input_coverage(
                self.db, self.storage, run_id=attempt.run_id, task_id=self.task_id,
                arguments=arguments,
            )
        if name == INPUT_SNAPSHOT_TOOL.name:
            return await read_task_input_snapshot(
                self.db, self.storage, run_id=attempt.run_id, requester_task_id=self.task_id,
                snapshot_id=arguments.snapshot_id, allowed_snapshot_ids=set(self.input_snapshots),
                offset=arguments.offset, limit=arguments.limit,
            )
        if name == "save_task_note":
            unknown = set(arguments.source_aliases) - self.sources.keys()
            if unknown:
                raise ToolOperationError("unknown_note_source")
            note = TaskNote(
                note_id=record.tool_call_id, note=arguments.note,
                unresolved_items=arguments.unresolved_items, next_actions=arguments.next_actions,
                sources={alias: FileEvidenceReference.model_validate(self.sources[alias])
                         for alias in dict.fromkeys(arguments.source_aliases)},
            )
            await self._validate_note_sources(attempt.run_id, note)
            return note.model_dump(mode="json")
        if name == "read_task_note":
            saved = await self.db.scalar(self._note_query(attempt.run_id).where(
                LlmModelToolCall.tool_call_id == arguments.note_id,
            ))
            if saved is None:
                raise ToolOperationError("note_unavailable", status="permission_denied")
            note = self._stored_note(saved)
            await self._validate_note_sources(attempt.run_id, note)
            return note.model_dump(mode="json")
        notes = list(await self.db.scalars(self._note_query(attempt.run_id).order_by(
            LlmModelToolCall.started_at, LlmModelToolCall.tool_call_id,
        ).offset(arguments.offset).limit(arguments.limit + 1)))
        return {
            "notes": [{"note_id": saved.tool_call_id, "created_at": saved.started_at.isoformat()}
                      for saved in notes[:arguments.limit]],
            "next_offset": arguments.offset + arguments.limit
            if len(notes) > arguments.limit else None,
        }

    def _note_query(self, run_id):
        """Select only successful note saves from the exact task and Run, including after reload."""
        return select(LlmModelToolCall).join(LlmModelAttempt).where(
            LlmModelAttempt.run_id == run_id, LlmModelAttempt.task_id == self.task_id,
            LlmModelToolCall.tool_name == "save_task_note", LlmModelToolCall.status == "completed",
        ).execution_options(populate_existing=True)

    def _stored_note(self, record):
        """Validate restored content and IDs; never guess an unknown historical schema."""
        note = TaskNote.model_validate((record.output_json or {}).get("result"))
        if note.note_id != record.tool_call_id:
            raise ResourceConflictError("Saved note identity does not match its tool call")
        return note

    async def _validate_note_sources(self, run_id, note):
        """Reauthorize exact original reads; reference validity does not verify note prose."""
        resolver = WorkerEvidenceService(self.db, self.storage)
        for reference in note.sources.values():
            await resolver.resolve(run_id, self.allowed_task_ids, reference)

    async def validate_history(self, run_id, records):
        """Recheck source grants and ownership before sending note/read/input history."""
        await super().validate_history(run_id, records)
        for record in records:
            if (record.tool_name not in NOTE_NAMES | {
                    READ_COVERAGE_TOOL.name, INPUT_COVERAGE_TOOL.name, INPUT_SNAPSHOT_TOOL.name,
                    DELEGATE_TOOL.name}
                    or record.status != "completed"):
                continue
            attempt = await self.db.get(LlmModelAttempt, record.attempt_id)
            if attempt is None or attempt.task_id != self.task_id or attempt.run_id != run_id:
                raise ResourceConflictError("Task note history belongs to another task")
            if record.tool_name == INPUT_SNAPSHOT_TOOL.name:
                result = record.output_json["result"]
                if result["snapshot_id"] not in self.input_snapshots or result["run_id"] != run_id:
                    raise ResourceConflictError("Input snapshot history is no longer granted")
                captured = await load_task_input_snapshot_manifest(
                    self.db, self.storage, run_id=run_id, snapshot_id=result["snapshot_id"],
                )
                if result["task_id"] != captured["task_id"]:
                    raise ResourceConflictError("Input snapshot history has a different task")
            if record.tool_name in {INPUT_COVERAGE_TOOL.name, INPUT_SNAPSHOT_TOOL.name}:
                await authorize_input_coverage_history(
                    self.db, run_id=run_id, result=record.output_json["result"],
                )
            if record.tool_name in {"save_task_note", "read_task_note"}:
                note = TaskNote.model_validate(record.output_json["result"])
                await self._validate_note_sources(run_id, note)
