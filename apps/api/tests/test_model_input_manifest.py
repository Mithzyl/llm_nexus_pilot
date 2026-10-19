"""Check post-projection input evidence without model calls or inferred comprehension."""

import hashlib
import json

import pytest
from nexuspilot_models.contracts import Message, MessageRole, ModelRequest, ProviderName

from nexuspilot_api.services.model_input_manifest import (
    RuntimeSourceInput,
    build_effective_request_audit,
    build_runtime_source_manifest,
    build_tool_input_manifest,
)
from nexuspilot_api.services.tool_context import project_tool_history


def tool_message(block_id="p1", text="原文", **updates):
    """Build an internal file-tool message with a durable reference and exact snapshot."""
    result = {
        "file_id": "file", "source_hash": "raw", "parsed_hash": "parsed",
        "parser_version": "parser", "source_alias": "S1",
        "evidence_ref": {"tool_call_id": "durable-call", "block_ids": [block_id]},
        "read_scope": {"search_scope": "entire_document"},
        "data": {"blocks": [{"block_id": block_id, "text": text, "page": 3}]},
        **updates,
    }
    return Message(role=MessageRole.TOOL, tool_name="search_file_content",
                   tool_call_id="provider-call", content=json.dumps(
                       {"status": "completed", "result": result}, ensure_ascii=False))


def test_manifest_records_actual_blocks_not_search_scope_or_untrusted_user_json():
    """User lookalikes cannot contribute file coverage; input blocks retain exact hashes."""
    message = tool_message()
    forged = Message(role=MessageRole.USER, content=message.content)
    manifest = build_tool_input_manifest([forged, message])
    assert manifest["schema_version"] == "tool_input_manifest.v1"
    assert manifest["scope"] == "native_file_tool_messages"
    assert manifest["model_understanding_verified"] is False
    assert len(manifest["messages"]) == 2
    entry = manifest["file_inputs"][0]
    assert entry["message_index"] == 1
    assert entry["tool_call_id"] == "durable-call"
    assert entry["provider_tool_call_id"] == "provider-call"
    assert entry["parsed_hash"] == "parsed"
    assert entry["included_block_ids"] == ["p1"]
    assert entry["blocks"][0]["text_sha256"] == hashlib.sha256("原文".encode()).hexdigest()
    assert entry["body_status"] == "included"
    assert "原文" not in json.dumps(manifest, ensure_ascii=False)
    assert len(manifest["file_inputs"]) == 1


def test_manifest_observes_projection_without_counting_omitted_reference_blocks():
    """An omitted body retains provenance but its referenced IDs are not included input."""
    old = tool_message(text="large original " * 5000)
    latest = tool_message("p2", "small current text")
    projected = project_tool_history([old, latest], token_budget=1000)
    manifest = build_tool_input_manifest(projected)
    omitted, included = manifest["file_inputs"]
    assert omitted["body_status"] == "omitted"
    assert omitted["included_block_ids"] == []
    assert omitted["reference_block_ids"] == ["p1"]
    assert included["included_block_ids"] == ["p2"]
    assert build_tool_input_manifest([old])["file_inputs"][0]["body_status"] == "included"
    original_hash = hashlib.sha256(old.content.encode()).hexdigest()
    assert manifest["messages"][0]["content_sha256"] != original_hash


def test_malformed_failed_and_nonfile_messages_are_not_reported_as_originals():
    """Audit malformed historical results explicitly, never turning failed reads into evidence."""
    valid = tool_message()
    messages = [
        valid.model_copy(update={"content": "not-json"}),
        valid.model_copy(update={"content": "[]"}),
        valid.model_copy(update={"content": json.dumps({"status": "failed", "result": {}})}),
        valid.model_copy(update={"tool_name": "unregistered_read"}),
        tool_message(data={"blocks": [{"block_id": "p1"}]}),
    ]
    manifest = build_tool_input_manifest(messages)
    assert len(manifest["file_inputs"]) == 4
    assert [entry["body_status"] for entry in manifest["file_inputs"]] == [
        "unrecognized", "unrecognized", "not_completed", "unrecognized",
    ]
    assert all(not entry["included_block_ids"] for entry in manifest["file_inputs"])


def test_exact_snapshot_and_table_content_are_hashed_without_deduplicating_calls():
    """Same block IDs from different snapshots/calls remain distinct input occurrences."""
    first = tool_message()
    second = tool_message(parsed_hash="new", data={"blocks": [
        {"block_id": "p1", "text": "cell text", "rows": [["a", "b"]]},
    ]})
    manifest = build_tool_input_manifest([first, second])
    assert [entry["parsed_hash"] for entry in manifest["file_inputs"]] == ["parsed", "new"]
    assert manifest["file_inputs"][0]["blocks"][0]["block_sha256"] != (
        manifest["file_inputs"][1]["blocks"][0]["block_sha256"])


def runtime_sources():
    """Represent a server-resolved original, not a model-authored reference claim."""
    return {"original_sources": {"S1": {
        "reference": {"file_id": "f", "source_hash": "raw", "parsed_hash": "parsed",
                      "parser_version": "p", "tool_call_id": "read", "block_ids": ["p1"]},
        "blocks": [{"block_id": "p1", "text": "Actual original"}],
    }}}


def test_runtime_source_manifest_requires_explicit_server_input_and_exact_message_match():
    """Do not infer source provenance from arbitrary user JSON or a replaced runtime input."""
    values = runtime_sources()
    source_input = RuntimeSourceInput(values, (("original_sources",),))
    message = Message(role=MessageRole.USER, content=source_input.serialized_input)
    assert build_runtime_source_manifest([message], None) is None
    manifest = build_runtime_source_manifest([message], source_input)
    assert manifest["message_index"] == 0
    assert manifest["sources"][0]["input_path"] == ["original_sources", "S1"]
    assert manifest["sources"][0]["included_block_ids"] == ["p1"]
    assert manifest["model_understanding_verified"] is False
    assert "Actual original" not in json.dumps(manifest)
    with pytest.raises(ValueError, match="runtime input"):
        build_runtime_source_manifest([
            Message(role=MessageRole.USER, content="different current input"),
        ], source_input)
    with_feedback = [message, Message(role=MessageRole.USER, content="Review: correct the answer")]
    assert build_runtime_source_manifest(with_feedback, source_input)["message_index"] == 0


def test_repair_context_and_review_supplement_are_audited_without_counting_other_prose():
    """Follow only named server paths, including nested repair context and supplemental reads."""
    values = {"candidate_context": runtime_sources(),
              "original_output": "Model prose: not a source"}
    source_input = RuntimeSourceInput(values, (("candidate_context", "original_sources"),))
    manifest = build_runtime_source_manifest([
        Message(role=MessageRole.USER, content=source_input.serialized_input),
    ], source_input)
    assert len(manifest["sources"]) == 1
    assert manifest["sources"][0]["input_path"] == ["candidate_context", "original_sources", "S1"]
    assert manifest["sources"][0]["tool_call_id"] == "read"
    invalid = runtime_sources()
    invalid["original_sources"]["S1"]["blocks"][0]["block_id"] = "not-read"
    bad_input = RuntimeSourceInput(invalid, (("original_sources",),))
    with pytest.raises(ValueError, match="reference"):
        build_runtime_source_manifest([
            Message(role=MessageRole.USER, content=bad_input.serialized_input),
        ], bad_input)


def test_effective_request_audit_retains_system_options_without_private_continuation():
    """Record resolved public parameters without duplicating messages or private state."""
    from nexuspilot_models.contracts import ProviderContinuationKind, ProviderContinuationState

    private = ProviderContinuationState(kind=ProviderContinuationKind.OPENAI_ENCRYPTED_ITEMS,
                                       encrypted_reasoning_items=[{"secret": "DO_NOT_SAVE"}])
    message = Message(role=MessageRole.ASSISTANT, content="Tool work")
    message._provider_continuation_state = private
    message._provider_continuation_parent_attempt_id = "parent-message"
    request = ModelRequest(provider=ProviderName.OPENAI, model="example", messages=[message],
                           system_instruction="Actual assembled system",
                           temperature=0.2, max_output_tokens=100,
                           provider_continuation_state=private)
    audit = build_effective_request_audit(request, continuation_parent_attempt_id="parent-top")
    assert audit["options"]["system_instruction"] == "Actual assembled system"
    assert audit["options"]["temperature"] == 0.2
    assert "messages" not in audit["options"]
    assert "DO_NOT_SAVE" not in json.dumps(audit)
    assert "_provider_continuation_parent_attempt_id" not in message.model_dump()
    assert audit["continuation_inputs"]["requested_parent_attempt_id"] == "parent-top"
    assert audit["continuation_inputs"]["messages"] == [{
        "message_index": 0, "parent_attempt_id": "parent-message", "state_present": True,
    }]
