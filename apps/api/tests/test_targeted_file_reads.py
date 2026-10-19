"""Verify search hits locate original blocks without scanning preceding file content."""

import pytest
from pydantic import ValidationError
from test_parsed_file_ranges import document_fixture

from nexuspilot_api.services.agent_file_tool_service import (
    ContentArguments,
    content_start_offset,
    search_read_targets,
)
from nexuspilot_api.services.file_parsing_service import search_file_content


async def test_correction_instruction_follows_preserved_tool_history():
    """A correction must follow the tool results it corrects, not precede them."""
    from nexuspilot_models.contracts import Message, ModelRequest, ToolCall

    from nexuspilot_api.services.model_response_service import ModelInvocationService

    request = ModelRequest(
        provider="openai",
        model="fixture",
        messages=[
            Message(role="user", content="original question"),
            Message(role="user", content='{"output_repair": {"validation_errors": []}}'),
        ],
    )
    history = [
        Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="read-1", name="read_file_content", arguments={})],
        ),
        Message(
            role="tool",
            tool_name="read_file_content",
            tool_call_id="read-1",
            content="original source",
        ),
    ]
    actual = await ModelInvocationService._append_tool_history(
        None,
        request,
        None,
        history,
        tool_history_before_input=True,
    )
    assert actual.messages[-1] == request.messages[-1]
    assert actual.messages[1:3] == history
    assert request.messages[1].role.value == "user"


def test_model_sources_exclude_internal_whole_file_block_inventory():
    """Keep quoted originals and coverage but avoid duplicating all file IDs per citation."""
    from nexuspilot_api.services.worker_evidence_service import model_evidence_bundle

    source = {
        "all_parsed_block_ids": [f"b{i}" for i in range(1542)],
        "blocks": [{"block_id": "b95", "text": "original"}],
        "reference": {"block_ids": ["b95"]},
        "read_scope": {"has_more": True},
    }
    bundle = {"claims": [{"sources": [source]}], "unresolved_items": [], "hypotheses": []}
    projected = model_evidence_bundle(bundle)
    result = projected["claims"][0]["sources"][0]
    assert "all_parsed_block_ids" not in result
    assert result["parsed_document_block_count"] == 1542
    assert result["blocks"] == source["blocks"]
    assert result["reference"] == source["reference"]
    assert result["read_scope"] == source["read_scope"]
    assert len(source["all_parsed_block_ids"]) == 1542


def test_search_target_uses_source_offset_not_match_offset():
    """Locate a last-page hit directly, keeping source and match offsets separate."""
    document = document_fixture(
        "application/pdf",
        [{"block_id": f"b{i}", "text": "needle" if i in (85, 95) else "other"} for i in range(100)],
    )
    page = search_file_content(document, "needle", offset=1, limit=1)
    target = search_read_targets(document, page, "f" * 36)[0]
    assert target["source_offset"] == 95
    arguments = ContentArguments.model_validate(target["read_arguments"])
    assert content_start_offset(document, arguments) == 90
    assert arguments.block_id == "b95"
    assert page.next_offset is None


def test_targeted_read_rejects_missing_or_conflicting_locations():
    """Never silently turn an invalid anchor into a beginning-of-file read."""
    document = document_fixture("application/pdf", [{"block_id": "b0", "text": "text"}])
    with pytest.raises(ValueError):
        content_start_offset(document, ContentArguments(file_id="f" * 36, block_id="missing"))
    with pytest.raises(ValidationError):
        ContentArguments(file_id="f" * 36, block_id="b0", offset=3)
    assert content_start_offset(document, ContentArguments(file_id="f" * 36, block_id="b0")) == 0


async def test_owned_search_hit_can_be_read_directly(client, test_database_session_factory):
    """Search persisted text and read nearby source without walking earlier pages."""
    from test_user_files import reserve_file

    from nexuspilot_api.infrastructure.object_storage import get_object_storage
    from nexuspilot_api.main import app
    from nexuspilot_api.services.agent_file_tool_service import (
        AgentFileToolService,
        SearchArguments,
    )

    content = "\n".join(
        f"Line {i}: {'needle' if i == 95 else 'other'}" for i in range(100)
    ).encode()
    owner = (await client.get("/api/v1/auth/me")).json()["user_id"]
    session = (await client.post("/api/v1/sessions", json={"user_id": owner})).json()
    file = (await reserve_file(client, content)).json()
    assert (
        await client.put(f"/api/v1/files/{file['file_id']}/content", content=content)
    ).status_code == 200
    turn = (
        await client.post(
            f"/api/v1/sessions/{session['session_id']}/turns",
            json={
                "user_id": owner,
                "content_text": "Locate needle",
                "file_ids": [file["file_id"]],
            },
        )
    ).json()
    async with test_database_session_factory() as db:
        tools = AgentFileToolService(db, app.dependency_overrides[get_object_storage]())
        result = await tools._read(
            turn["run"]["run_id"],
            "search_file_content",
            SearchArguments(file_id=file["file_id"], query="needle"),
        )
        target = result["data"]["read_targets"][0]
        read = await tools._read(
            turn["run"]["run_id"],
            "read_file_content",
            ContentArguments.model_validate(target["read_arguments"]),
        )
        assert read["read_scope"]["offset"] == 90
        assert "Line 95: needle\n" in [block["text"] for block in read["data"]["blocks"]]
        assert read["source_hash"] == result["source_hash"]
        assert read["parsed_hash"] == result["parsed_hash"]
