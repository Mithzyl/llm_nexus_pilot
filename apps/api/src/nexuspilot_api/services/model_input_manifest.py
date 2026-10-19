"""Describe native file text in effective model input, not tool execution or comprehension.

The manifest lives beside private effective messages in the attempt's object-store
request. It is not a permission grant, a provider receipt, or whole-document read
coverage. User prose and reviewer source bundles are deliberately outside this
version's scope; old requests without a manifest have unknown coverage.
"""

import hashlib
import json

from nexuspilot_models.contracts import Message, MessageRole

from nexuspilot_api.services.tool_context import FILE_BODY_TOOLS


def _json_hash(value: object) -> str:
    """Hash a canonical JSON value so stored message/block records can be compared."""
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def build_tool_input_manifest(messages: list[Message]) -> dict:
    """Record exact post-projection file block occurrences without copying their text.

Only successful internal native file results with well-formed text blocks count.
An omitted result retains its reference IDs separately; those IDs never count as
included text. Malformed historical envelopes are marked unrecognized rather
than blocking an otherwise valid request or inventing coverage.
"""
    manifest = {
        "schema_version": "tool_input_manifest.v1",
        "scope": "native_file_tool_messages",
        "model_understanding_verified": False,
        "messages": [],
        "file_inputs": [],
    }
    for index, message in enumerate(messages):
        manifest["messages"].append({
            "message_index": index,
            "role": message.role.value,
            "content_sha256": hashlib.sha256(message.content.encode()).hexdigest(),
            "message_sha256": _json_hash(message.model_dump(mode="json")),
        })
        if message.role is not MessageRole.TOOL or message.tool_name not in FILE_BODY_TOOLS:
            continue
        entry = {
            "message_index": index,
            "tool_name": message.tool_name,
            "provider_tool_call_id": message.tool_call_id,
            "body_status": "unrecognized",
            "included_block_ids": [],
            "reference_block_ids": [],
            "blocks": [],
        }
        manifest["file_inputs"].append(entry)
        try:
            envelope = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(envelope, dict):
            continue
        if envelope.get("status") != "completed":
            entry["body_status"] = "not_completed"
            continue
        result = envelope.get("result")
        if not isinstance(result, dict):
            continue
        for name in ("file_id", "source_hash", "parsed_hash", "parser_version", "source_alias"):
            if isinstance(result.get(name), str):
                entry[name] = result[name]
        reference = result.get("evidence_ref")
        if isinstance(reference, dict):
            entry["tool_call_id"] = reference.get("tool_call_id")
            ids = reference.get("block_ids")
            if isinstance(ids, list) and all(isinstance(value, str) for value in ids):
                entry["reference_block_ids"] = ids
        if result.get("context_body_omitted") is True:
            entry["body_status"] = "omitted"
            continue
        data = result.get("data")
        blocks = data.get("blocks") if isinstance(data, dict) else None
        if not isinstance(blocks, list) or not all(
            isinstance(block, dict) and isinstance(block.get("block_id"), str)
            and isinstance(block.get("text"), str) for block in blocks
        ):
            continue
        entry["body_status"] = "included" if blocks else "empty"
        entry["included_block_ids"] = [block["block_id"] for block in blocks]
        entry["blocks"] = [{
            "block_id": block["block_id"],
            "text_sha256": hashlib.sha256(block["text"].encode()).hexdigest(),
            "block_sha256": _json_hash(block),
        } for block in blocks]
    return manifest
