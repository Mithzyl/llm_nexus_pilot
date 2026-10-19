"""Describe trusted built-in tools without importing model-authored code or granting access.

This catalog has one active, fixed version per native tool name. Visibility is
filtered before search; execution must separately check current resource access.
"""

import re
from dataclasses import dataclass

from nexuspilot_models.contracts import ToolDefinition
from pydantic import BaseModel


@dataclass(frozen=True)
class RegisteredTool:
    """Share typed input/output metadata between validation, discovery and Provider requests."""

    name: str
    version: str
    description: str
    arguments: type[BaseModel]
    output: type[BaseModel]
    required_capabilities: frozenset[str]
    keywords: tuple[str, ...] = ()
    initially_loaded: bool = False
    risk_level: str = "read_only"

    def __post_init__(self) -> None:
        """Reject ambiguous identities and missing schemas before any tool is advertised."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise ValueError("Invalid tool name")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.version):
            raise ValueError("Invalid tool version")
        if not self.description.strip() or len(self.description) > 2048:
            raise ValueError("Invalid tool description")
        if not issubclass(self.arguments, BaseModel) or not issubclass(self.output, BaseModel):
            raise ValueError("Tool input and output require typed schemas")
        if self.risk_level not in {"read_only", "task_write"}:
            raise ValueError("Unsupported built-in tool risk level")

    def definition(self) -> ToolDefinition:
        """Publish callable input metadata without internal execution references or secrets."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.arguments.model_json_schema(),
        )

    def describe(self) -> dict:
        """Return the pinned schemas and scope; loading never grants additional permissions."""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "input_schema": self.arguments.model_json_schema(),
            "output_schema": self.output.model_json_schema(),
            "required_capabilities": sorted(self.required_capabilities),
            "available": True,
            "availability_scope": "tool_implementation_only; resource_authorization_on_each_call",
        }


class ToolCatalog:
    """Search trusted metadata after filtering server-derived capabilities."""

    def __init__(self, entries: tuple[RegisteredTool, ...]):
        """Snapshot one active definition per tool; do not allow names to shadow earlier entries."""
        if len({entry.name for entry in entries}) != len(entries):
            raise ValueError("Duplicate active tool name")
        self.entries = tuple(sorted(entries, key=lambda entry: entry.name))

    def get(self, name: str, version: str, capabilities: frozenset[str]) -> RegisteredTool | None:
        """Return the exact permitted version; hidden and unknown tools both return None."""
        return next(
            (
                entry
                for entry in self.entries
                if entry.name == name
                and entry.version == version
                and entry.required_capabilities <= capabilities
            ),
            None,
        )

    def search(self, query: str, *, capabilities: frozenset[str], offset: int, limit: int) -> dict:
        """Page case-insensitive literal matches; exclude invisible entries before counting."""
        words = query.casefold().split()
        matches = [
            entry
            for entry in self.entries
            if entry.required_capabilities <= capabilities
            and all(
                word in " ".join((entry.name, entry.description, *entry.keywords)).casefold()
                for word in words
            )
        ]
        return {
            "items": [
                {
                    "name": entry.name,
                    "version": entry.version,
                    "description": entry.description,
                    "available": True,
                }
                for entry in matches[offset : offset + limit]
            ],
            "next_offset": offset + limit if offset + limit < len(matches) else None,
        }
