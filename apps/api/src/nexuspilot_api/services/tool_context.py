"""Estimate working-context tokens and omit reloadable old file bodies, never evidence.

cl100k_base is a proxy for providers without a registered native tokenizer. The
20% allowance is not a guaranteed bound or billing usage. Prewarm its vocabulary
at environment setup; no model service is involved in encoding text.
"""

import json
import math
from functools import lru_cache

import tiktoken
from nexuspilot_models.contracts import Message, MessageRole

from nexuspilot_api.schemas.file_parsing import FileContentPage

TOKENIZER_NAME = "cl100k_base_proxy"
TOKENIZER_VERSION = "v1"
FILE_BODY_TOOLS = {"read_file_content", "read_file_table", "search_file_content"}


def bound_file_page(page: FileContentPage, *, offset: int, token_budget: int = 4096):
    """Return whole blocks within a working-page budget, with an honest next offset.

    A single oversized block is retained intact so the caller can report an
    explicit context error; it is never silently truncated or skipped.
    """
    selected = []
    total = 0
    for block in page.blocks:
        size = estimate_text_tokens(block.model_dump_json())
        if selected and total + size > token_budget:
            break
        selected.append(block)
        total += size
    next_offset = offset + len(selected)
    has_more = next_offset < page.total_blocks
    return page.model_copy(
        update={
            "blocks": selected,
            "has_more": has_more,
            "next_offset": next_offset if has_more else None,
        }
    )


def allow_tool_request(seen: dict, name: str, fingerprint: str, ordinal: int) -> bool:
    """Permit one spaced file reread while rejecting immediate repeats and cycles."""
    previous = seen.get(fingerprint)
    if previous is not None:
        last_ordinal, count = previous
        if name not in FILE_BODY_TOOLS or ordinal <= last_ordinal + 1 or count >= 2:
            return False
    else:
        count = 0
    seen[fingerprint] = (ordinal, count + 1)
    return True


@lru_cache(maxsize=1)
def _encoding():
    """Load the setup-cached vocabulary once; never fall back to byte counting."""
    return tiktoken.get_encoding("cl100k_base")


def estimate_text_tokens(text: str) -> int:
    """Count BPE tokens with a 20% cross-provider allowance; treat special text literally."""
    return max(1, math.ceil(len(_encoding().encode(text, disallowed_special=())) * 1.2))


def estimate_messages_tokens(messages: list[Message]) -> int:
    """Include native calls and private continuation, without logging sensitive content."""
    total = 0
    for message in messages:
        total += estimate_text_tokens(message.model_dump_json()) + 8
        state = message._provider_continuation_state
        if state is not None:
            total += estimate_text_tokens(state.model_dump_json())
    return total


def project_tool_history(messages: list[Message], *, token_budget: int) -> list[Message]:
    """Omit oldest reloadable bodies only when needed, preserving calls and latest batch.

    Durable tool results and the caller's history are untouched. A marker is not
    evidence: the model must reread before using omitted text as original source.
    Private continuation and assistant/tool pairing remain unchanged.
    """
    projected = list(messages)
    total = estimate_messages_tokens(projected)
    if total <= token_budget:
        return projected
    # Retain all tool results following the most recent assistant tool request.
    assistants = [i for i, message in enumerate(messages) if message.tool_calls]
    keep_from = assistants[-1] if assistants else max(0, len(messages) - 1)
    for index, message in enumerate(messages[:keep_from]):
        if message.role is not MessageRole.TOOL or message.tool_name not in FILE_BODY_TOOLS:
            continue
        try:
            envelope = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(envelope, dict) or not isinstance(envelope.get("result"), dict):
            continue
        result = envelope["result"]
        if not isinstance(result.get("data"), dict) or "blocks" not in result["data"]:
            continue
        data = result["data"]
        compact = {
            **result,
            "data": {key: value for key, value in data.items() if key != "blocks"},
            "context_body_omitted": True,
            "reload_instruction": "Original text omitted from working context, not deleted. "
            "Use file_id and read_scope to reread or search_file_content for specific text. "
            "Do not infer that unread or omitted content is absent.",
        }
        replacement = message.model_copy(
            update={"content": json.dumps({**envelope, "result": compact}, ensure_ascii=False)}
        )
        savings = estimate_messages_tokens([message]) - estimate_messages_tokens([replacement])
        if savings <= 0:
            continue
        projected[index] = replacement
        total -= savings
        if total <= token_budget:
            return projected
    raise ValueError(
        "Tool history exceeds model context after omitting reloadable old file bodies; "
        "reduce the read range or narrow the question"
    )
