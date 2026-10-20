"""Collect incomplete normalized stream evidence without approving tools or reasoning.

This is received-output evidence, not a completed ModelResponse or a raw SSE log.
Callers may persist it privately on interruption; it must never execute tools.
"""

import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from nexuspilot_models.contracts import StreamEvent, StreamEventType

USAGE_FIELDS = frozenset({
    "input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens",
    "cache_miss_tokens", "reasoning_tokens",
})


class ModelStreamProgress:
    """Accumulate public body/argument fragments and counts, never private reasoning text."""

    def __init__(self) -> None:
        """Start with unknown completion and usage rather than invented zero-token billing."""
        self.text_parts: list[str] = []
        self.tool_fragments: dict[str, dict[str, Any]] = {}
        self.usage: dict[str, int] = {}
        self.first_output_at: str | None = None
        self.first_text_at: str | None = None
        self.last_output_at: str | None = None
        self.reasoning_characters = 0
        self.event_count = 0
        self.started = time.perf_counter()

    def record(self, event: StreamEvent) -> None:
        """Observe normalized events; tool fragments remain unvalidated and non-executable."""
        self.event_count += 1
        data = event.data
        changed = False
        now = datetime.now(UTC).isoformat()
        if event.type == StreamEventType.TEXT_DELTA:
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                self.text_parts.append(delta)
                self.first_text_at = self.first_text_at or now
                changed = True
        elif event.type in {
            StreamEventType.REASONING_RAW_DELTA, StreamEventType.REASONING_SUMMARY_DELTA,
        }:
            delta = data.get("delta")
            if isinstance(delta, str) and delta:
                self.reasoning_characters += len(delta)
                changed = True
        elif event.type == StreamEventType.TOOL_CALL_DELTA:
            key = str(data.get("index", data.get("id", "")))
            fragment = self.tool_fragments.setdefault(key, {"arguments_delta": ""})
            for field in ("id", "name"):
                if isinstance(data.get(field), str):
                    fragment[field] = data[field]
            delta = data.get("arguments_delta")
            if isinstance(delta, str) and delta:
                fragment["arguments_delta"] += delta
                changed = True
            # Gemini exposes a decoded argument object, but an interrupted turn
            # still does not authorize execution of that object.
            if isinstance(data.get("arguments"), dict):
                fragment["arguments"] = data["arguments"]
                changed = True
        elif event.type == StreamEventType.USAGE:
            for field in USAGE_FIELDS:
                value = data.get(field)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    self.usage[field] = value
        if changed:
            self.first_output_at = self.first_output_at or now
            self.last_output_at = now

    def snapshot(self) -> dict[str, Any]:
        """Return incomplete evidence; character counts are not token or cost estimates."""
        text = "".join(self.text_parts)
        return {
            "schema_version": "model_stream_progress.v1",
            "response_complete": False,
            "usage_complete": False,
            "text": text,
            "text_characters": len(text),
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "tool_fragments": list(self.tool_fragments.values()),
            "usage": dict(self.usage),
            "first_output_at": self.first_output_at,
            "first_text_at": self.first_text_at,
            "last_output_at": self.last_output_at,
            "reasoning_characters": self.reasoning_characters,
            "event_count": self.event_count,
            "elapsed_ms": int((time.perf_counter() - self.started) * 1000),
        }


def interrupted_stream_progress(error: BaseException) -> dict[str, Any] | None:
    """Find trusted local progress through timeout/error translation without parsing messages."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        progress = getattr(current, "stream_progress", None)
        if isinstance(progress, dict):
            return progress
        current = current.__cause__ or current.__context__
    return None
