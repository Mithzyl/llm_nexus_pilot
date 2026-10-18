"""Verify token estimates and reversible, protocol-preserving history projection."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import tiktoken
from nexuspilot_models.contracts import (
    Message,
    MessageRole,
    ModelRequest,
    ProviderContinuationKind,
    ProviderContinuationState,
    ProviderName,
    ToolCall,
)

from nexuspilot_api.schemas.file_parsing import FileContentPage, ParsedFileBlock, ParsedFileDocument
from nexuspilot_api.services.file_parsing_service import search_file_content
from nexuspilot_api.services.model_response_service import ModelInvocationService
from nexuspilot_api.services.tool_context import (
    allow_tool_request,
    bound_file_page,
    estimate_text_tokens,
    project_tool_history,
)


def test_token_estimate_uses_encoding_not_utf8_bytes():
    """Chinese and ASCII inputs use a documented proxy tokenizer, not byte length."""
    for text in ["文件内容需要按需读取。" * 100, "hello world " * 100]:
        count = len(tiktoken.get_encoding("cl100k_base").encode(text))
        assert count <= estimate_text_tokens(text) <= count * 1.2 + 1
        assert estimate_text_tokens(text) < len(text.encode())


def test_projection_preserves_latest_result_and_source_metadata():
    """Only older file bodies leave working context; original records remain intact."""
    result = {
        "result": {
            "file_id": "file",
            "source_hash": "snapshot",
            "read_scope": {"offset": 0, "limit": 50, "has_more": True},
            "evidence_ref": {"tool_call_id": "call"},
            "data": {"blocks": [{"block_id": "p1", "text": "long text " * 4000}]},
        }
    }
    old = Message(
        role=MessageRole.TOOL,
        tool_call_id="call",
        tool_name="read_file_content",
        content=json.dumps(result),
    )
    latest = old.model_copy(update={"tool_call_id": "latest", "content": "latest result"})
    projected = project_tool_history([old, latest], token_budget=1000)
    assert projected[-1] is latest
    compacted = json.loads(projected[0].content)["result"]
    assert compacted["read_scope"] == result["result"]["read_scope"]
    assert compacted["evidence_ref"] == result["result"]["evidence_ref"]
    assert compacted["source_hash"] == "snapshot"
    assert compacted["context_body_omitted"] is True
    assert "long text" in old.content
    assert projected[0].tool_call_id == "call"


def test_projection_does_not_silently_truncate_latest_or_nonfile_tools():
    """Unreducible history fails explicitly instead of deleting required results."""
    latest = Message(
        role=MessageRole.TOOL,
        tool_call_id="call",
        tool_name="read_file_content",
        content="large " * 3000,
    )
    with pytest.raises(ValueError, match="context"):
        project_tool_history([latest], token_budget=100)


def test_page_budget_preserves_blocks_and_advances_by_returned_count():
    """Paging budgets change continuation, never truncate original block text."""
    blocks = [
        ParsedFileBlock(block_id=f"line:{i}", kind="text", text="hello world " * 400)
        for i in range(3)
    ]
    page = FileContentPage(blocks=blocks, total_blocks=10, next_offset=8, has_more=True)
    bounded = bound_file_page(page, offset=5, token_budget=1500)
    assert bounded.blocks == blocks[:1]
    assert bounded.next_offset == 6
    assert bounded.has_more is True
    assert len(page.blocks) == 3


def test_repeated_reads_allow_one_spaced_reread_but_not_no_progress_loop():
    """A reread after another range is allowed once, not unlimited cycling."""
    seen = {}
    assert allow_tool_request(seen, "read_file_content", "range-a", 0)
    assert not allow_tool_request(seen, "read_file_content", "range-a", 1)
    assert allow_tool_request(seen, "read_file_content", "range-a", 2)
    assert not allow_tool_request(seen, "read_file_content", "range-a", 4)


async def test_gateway_recounts_legacy_byte_estimate_before_appending_tools():
    """The real gateway does not reuse historical byte units as the new token estimate."""
    service = object.__new__(ModelInvocationService)
    service.db_session = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(
                input_token_estimate=9000, token_budget=2000, reserved_output_tokens=0
            )
        )
    )
    request = ModelRequest(
        provider=ProviderName.OPENAI,
        model="test",
        messages=[Message(role=MessageRole.USER, content="读取文件")],
    )
    history = [
        Message(
            role=MessageRole.TOOL,
            tool_call_id="call",
            tool_name="read_file_content",
            content="文件内容。" * 100,
        )
    ]
    updated = await service._append_tool_history(request, "historical-context", history)
    assert updated.messages[-1] == history[0]


def test_projection_keeps_assistant_continuation_and_entire_latest_batch():
    """Compaction keeps private state and IDs paired, including parallel-call batches."""
    assistant = Message(
        role=MessageRole.ASSISTANT,
        content="",
        tool_calls=[ToolCall(id="a", name="read_file_content", arguments={})],
    )
    assistant._provider_continuation_state = ProviderContinuationState(
        kind=ProviderContinuationKind.OPENAI_ENCRYPTED_ITEMS,
        encrypted_reasoning_items=[{"type": "reasoning", "encrypted_content": "opaque"}],
    )
    old = Message(
        role=MessageRole.TOOL,
        tool_call_id="a",
        tool_name="read_file_content",
        content=json.dumps(
            {
                "result": {
                    "file_id": "file",
                    "data": {"blocks": [{"block_id": "a", "text": "old " * 8000}]},
                }
            }
        ),
    )
    last = Message(
        role=MessageRole.ASSISTANT,
        content="",
        tool_calls=[ToolCall(id=x, name="read_file_content", arguments={}) for x in ["b", "c"]],
    )
    results = [
        Message(
            role=MessageRole.TOOL,
            tool_call_id=x,
            tool_name="read_file_content",
            content="recent text",
        )
        for x in ["b", "c"]
    ]
    projected = project_tool_history([assistant, old, last, *results], token_budget=2000)
    assert projected[0] is assistant
    assert projected[0]._provider_continuation_state is assistant._provider_continuation_state
    assert projected[2:] == [last, *results]


def test_search_reaches_unread_final_block_and_paginates_matches():
    """Search operates on the whole stored extraction, not previously loaded pages."""
    document = ParsedFileDocument(
        source_id="file",
        source_hash="a" * 64,
        filename="long.txt",
        media_type="text/plain",
        parser_name="test",
        parser_version="1",
        status="complete",
        blocks=[
            ParsedFileBlock(
                block_id=f"line:{i}", kind="text", text="needle" if i in [998, 999] else "other"
            )
            for i in range(1000)
        ],
    )
    first = search_file_content(document, "needle", offset=0, limit=1)
    assert first.blocks[0].block_id == "line:998"
    assert first.next_offset == 1
    last = search_file_content(document, "needle", offset=first.next_offset, limit=1)
    assert last.blocks[0].block_id == "line:999"
    assert last.has_more is False
