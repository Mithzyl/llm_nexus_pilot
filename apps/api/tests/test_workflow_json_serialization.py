"""Tests for deterministic JSON serialization in workflow policy services."""

import json

from nexuspilot_api.features.agent_runtime.services.workflow_policy import (
    serialize_agent_generated_json,
)


def test_serialize_agent_generated_json_deterministic_order():
    """Verify that dictionaries with different insertion orders produce identical JSON strings."""
    dict_a = {"b": 1, "a": 2}
    dict_b = {"a": 2, "b": 1}
    assert serialize_agent_generated_json(dict_a) == serialize_agent_generated_json(dict_b)

    nested_a = {"outer": {"z": 1, "a": 2}, "top": 3}
    nested_b = {"top": 3, "outer": {"a": 2, "z": 1}}
    assert serialize_agent_generated_json(nested_a) == serialize_agent_generated_json(nested_b)


def test_serialize_agent_generated_json_chinese_and_list_order():
    """Verify that Chinese characters are not escaped and list element order is preserved."""
    data = {"text": "你好世界", "items": [3, 1, 2]}
    expected = '{"items":[3,1,2],"text":"你好世界"}'
    result = serialize_agent_generated_json(data)
    assert result == expected
    assert json.loads(result) == data
