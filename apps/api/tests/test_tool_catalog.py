"""Specify deterministic tool discovery, fixed versions and capability-filtered visibility."""

import pytest
from pydantic import BaseModel


class ExampleArguments(BaseModel):
    """Provide a minimal schema for catalog rules without an execution environment."""

    query: str


def catalog_entry(name="read_example", **changes):
    """Create explicit registry metadata, never executable code supplied by a model."""
    from nexuspilot_api.services.tool_catalog import RegisteredTool

    fields = dict(
        name=name,
        version="1.0.0",
        description="Read table 表格",
        arguments=ExampleArguments,
        output=ExampleArguments,
        required_capabilities=frozenset({"read"}),
        keywords=("table", "表格"),
        initially_loaded=False,
    )
    return RegisteredTool(**{**fields, **changes})


def test_catalog_search_does_not_expose_unpermitted_tools():
    """Filter before search and pagination so names and totals cannot reveal hidden tools."""
    from nexuspilot_api.services.tool_catalog import ToolCatalog

    catalog = ToolCatalog(
        (
            catalog_entry(),
            catalog_entry("secret_writer", required_capabilities=frozenset({"write"})),
        )
    )
    result = catalog.search("表格", capabilities=frozenset({"read"}), offset=0, limit=1)
    assert [item["name"] for item in result["items"]] == ["read_example"]
    assert result["next_offset"] is None
    assert (
        catalog.search("secret", capabilities=frozenset({"read"}), offset=0, limit=10)["items"]
        == []
    )
    assert catalog.get("secret_writer", "1.0.0", frozenset({"read"})) is None
    assert catalog.get("missing", "1.0.0", frozenset({"read"})) is None


def test_description_matches_executable_schema_and_fixed_version():
    """A discovered version describes exactly the schema used for Provider input and validation."""
    from nexuspilot_api.services.tool_catalog import ToolCatalog

    entry = catalog_entry()
    catalog = ToolCatalog((entry,))
    assert catalog.get(entry.name, "2.0.0", frozenset({"read"})) is None
    description = entry.describe()
    assert description["input_schema"] == entry.definition().input_schema
    assert description["output_schema"] == ExampleArguments.model_json_schema()
    assert description["version"] == "1.0.0"
    assert "implementation_reference" not in description


@pytest.mark.parametrize("invalid_name", ["", "../read", "read example"])
def test_catalog_rejects_invalid_names(invalid_name):
    """Tool names are protocol identifiers, never paths or command strings."""
    with pytest.raises(ValueError):
        catalog_entry(invalid_name)


def test_catalog_rejects_duplicate_active_names():
    """A native Provider tool name must resolve to exactly one immutable active version."""
    from nexuspilot_api.services.tool_catalog import ToolCatalog

    with pytest.raises(ValueError):
        ToolCatalog((catalog_entry(), catalog_entry(version="2.0.0")))


def test_loaded_tools_are_local_to_one_worker_and_recheck_capabilities():
    """Loading cannot change another Worker's schemas or override revoked server policy."""
    from nexuspilot_api.services.agent_file_tool_service import AgentFileToolService

    first = AgentFileToolService(None, None)
    second = AgentFileToolService(None, None)
    first.loaded_versions["get_file_outline"] = "1.0.0"
    assert "get_file_outline" in {tool.name for tool in first.definitions()}
    assert "get_file_outline" not in {tool.name for tool in second.definitions()}
    first.capabilities = frozenset()
    assert first.definitions() == []


def test_search_paginates_visible_matches_without_mutating_catalog():
    """Stable name ordering and continuation offsets preserve every permitted match."""
    from nexuspilot_api.services.tool_catalog import ToolCatalog

    catalog = ToolCatalog((catalog_entry("read_b"), catalog_entry("read_a")))
    first = catalog.search("table", capabilities=frozenset({"read"}), offset=0, limit=1)
    second = catalog.search(
        "table", capabilities=frozenset({"read"}), offset=first["next_offset"], limit=1
    )
    assert [first["items"][0]["name"], second["items"][0]["name"]] == ["read_a", "read_b"]
    assert second["next_offset"] is None
    assert (
        catalog.search("missing", capabilities=frozenset({"read"}), offset=0, limit=1)["items"]
        == []
    )
