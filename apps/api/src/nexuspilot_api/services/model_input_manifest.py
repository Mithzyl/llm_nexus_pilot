"""Describe prepared model input, not provider receipt or comprehension.

The manifest lives beside private effective messages in the attempt's object-store
request. It is not a permission grant, a provider receipt, or whole-document read
coverage. Server-designated source bundles are separate from native Tool messages;
arbitrary user JSON never establishes source provenance. Old absent audits remain unknown.
"""

import hashlib
import json
from dataclasses import dataclass

from nexuspilot_models.contracts import Message, MessageRole, ModelRequest

from nexuspilot_api.services.tool_context import FILE_BODY_TOOLS


@dataclass(frozen=True)
class RuntimeSourceInput:
    """Identify source-bearing paths in one server-assembled input, never an HTTP parameter."""

    values: dict
    source_paths: tuple[tuple[str, ...], ...]

    @property
    def serialized_input(self) -> str:
        """Use the same canonical serialization as Agent node input construction."""
        return json.dumps(self.values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_effective_request_audit(
    request: ModelRequest, *, continuation_parent_attempt_id: str | None = None,
) -> dict:
    """Save provider-neutral options and private-state references, never private state bytes.

    Messages live in effective_messages beside this audit. The requested top-level
    continuation reference is resolved after attempt creation; it is not proof of
    successful loading. Message references describe already loaded internal state.
    Provider wire conversion and HTTP credentials are outside this record.
    """
    options = request.model_dump(mode="json", exclude={"messages"})
    options["resolved_reasoning"] = (
        request.resolved_reasoning.model_dump(mode="json") if request.resolved_reasoning else None
    )
    options["native_input_capabilities"] = (
        request.native_input_capabilities.model_dump(mode="json")
        if request.native_input_capabilities else None
    )
    return {
        "schema_version": "effective_request.v1",
        "scope": "prepared_provider_neutral_request",
        "messages_field": "effective_messages",
        "options": options,
        "continuation_inputs": {
            "requested_parent_attempt_id": continuation_parent_attempt_id,
            "messages": [{
                "message_index": index,
                "parent_attempt_id": message._provider_continuation_parent_attempt_id,
                "state_present": message._provider_continuation_state is not None,
            } for index, message in enumerate(request.messages)
                if message._provider_continuation_state is not None
                or message._provider_continuation_parent_attempt_id is not None],
        },
    }


def build_runtime_source_manifest(
    messages: list[Message], source_input: RuntimeSourceInput | None,
) -> dict | None:
    """Hash originals at explicit server paths only after verifying the actual current input.

    Match the complete server-assembled JSON, not arbitrary nested JSON in user
    prose. Prior feedback may follow it in the continuing history. An absent exact
    input fails before dispatch; permissions remain the caller's resolver job.
    """
    if source_input is None:
        return None
    indices = [index for index, message in enumerate(messages)
               if message.role is MessageRole.USER
               and message.content == source_input.serialized_input]
    if not indices:
        raise ValueError("Server runtime input differs from effective model messages")
    sources = []
    for path in source_input.source_paths:
        value = source_input.values
        for field in path:
            if not isinstance(value, dict) or field not in value:
                raise ValueError("Expected runtime source path is missing")
            value = value[field]
        if not isinstance(value, dict):
            raise ValueError("Runtime source bundle is not a mapping")
        for alias, source in value.items():
            reference, blocks = source["reference"], source["blocks"]
            ids = [block["block_id"] for block in blocks]
            if len(set(ids)) != len(ids) or set(ids) != set(reference["block_ids"]):
                raise ValueError("Runtime original blocks differ from source reference")
            sources.append({
                "input_path": [*path, alias],
                **{key: reference[key] for key in (
                    "tool_call_id", "file_id", "source_hash", "parsed_hash", "parser_version",
                )},
                "included_block_ids": ids,
                "blocks": [{
                    "block_id": block["block_id"],
                    "text_sha256": hashlib.sha256(block["text"].encode()).hexdigest(),
                    "block_sha256": _json_hash(block),
                } for block in blocks],
            })
    return {
        "schema_version": "runtime_source_manifest.v1",
        "message_index": indices[-1],
        "input_sha256": hashlib.sha256(source_input.serialized_input.encode()).hexdigest(),
        "source_paths": [list(path) for path in source_input.source_paths],
        "sources": sources,
        "model_understanding_verified": False,
    }


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
