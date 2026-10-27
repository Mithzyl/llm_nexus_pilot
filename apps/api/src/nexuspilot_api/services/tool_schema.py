"""Validate the deliberately small JSON Schema subset used by task extensions.

Unsupported keywords fail registration rather than disappearing from validation.
This is not a general JSON Schema implementation; refs, unions and patterns are deferred.
"""

import json
import math


def check_schema(schema: dict, depth: int = 0) -> None:
    """Reject unsupported or ambiguous definitions before exposing them to a model."""
    if depth > 8 or not isinstance(schema, dict):
        raise ValueError("Invalid schema depth or type")
    kind = schema.get("type")
    common = {"type", "description", "title", "enum"}
    allowed = {
        "object": common | {"properties", "required", "additionalProperties"},
        "array": common | {"items", "maxItems", "minItems"},
        "string": common | {"maxLength", "minLength"},
        "integer": common | {"minimum", "maximum"},
        "number": common | {"minimum", "maximum"},
        "boolean": common,
        "null": common,
    }
    if not isinstance(kind, str) or kind not in allowed or set(schema) - allowed[kind]:
        raise ValueError("Unsupported schema")
    if len(json.dumps(schema, allow_nan=False).encode()) > 16384:
        raise ValueError("Schema too large")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if (
            not isinstance(properties, dict)
            or len(properties) > 32
            or not isinstance(required, list)
            or any(not isinstance(key, str) or key not in properties for key in required)
            or schema.get("additionalProperties") is not False
        ):
            raise ValueError("Objects require explicit closed properties")
        for child in properties.values():
            check_schema(child, depth + 1)
    if kind == "array":
        check_schema(schema.get("items"), depth + 1)
    for low, high in (("minItems", "maxItems"), ("minLength", "maxLength"), ("minimum", "maximum")):
        for key in (low, high):
            if key in schema:
                value = schema[key]
                if (
                    type(value) not in {int, float}
                    or (type(value) is float and not math.isfinite(value))
                    or (key not in {"minimum", "maximum"} and (type(value) is not int or value < 0))
                ):
                    raise ValueError("Invalid schema bound")
        if low in schema and high in schema and schema[low] > schema[high]:
            raise ValueError("Inverted schema bounds")
    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]:
            raise ValueError("Invalid enum")
        for value in schema["enum"]:
            validate_value({k: v for k, v in schema.items() if k != "enum"}, value)


def validate_value(schema: dict, value, depth: int = 0) -> None:
    """Validate JSON input/output exactly, without coercion or running supplied code."""
    if depth > 8:
        raise ValueError("Value nesting exceeds limit")
    kind = schema["type"]
    types = {
        "object": (dict,),
        "array": (list,),
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "null": (type(None),),
    }
    if type(value) not in types[kind]:
        raise ValueError("Value type does not match schema")
    if "enum" in schema and not any(
        type(value) is type(item) and value == item for item in schema["enum"]
    ):
        raise ValueError("Value outside enum")
    if kind == "object":
        properties = schema.get("properties", {})
        if set(value) - properties.keys() or set(schema.get("required", [])) - value.keys():
            raise ValueError("Unknown or missing property")
        for key, item in value.items():
            validate_value(properties[key], item, depth + 1)
    elif kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 10000):
            raise ValueError("Array length outside bounds")
        for item in value:
            validate_value(schema["items"], item, depth + 1)
    elif kind == "string":
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 65536):
            raise ValueError("String length outside bounds")
    elif kind in {"integer", "number"}:
        if (
            (type(value) is float and not math.isfinite(value))
            or value < schema.get("minimum", -math.inf)
            or value > schema.get("maximum", math.inf)
        ):
            raise ValueError("Number outside bounds")
