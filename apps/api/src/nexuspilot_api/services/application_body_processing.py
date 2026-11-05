"""Process complete main-Agent text through an approved caller, retaining ambiguous writes."""

import asyncio
import json
import math

import httpx
from sqlalchemy import select, update

from nexuspilot_api.core.errors import InvalidRequestError
from nexuspilot_api.infrastructure.object_storage import ObjectStorageError
from nexuspilot_api.models import ApplicationResponseBinding, ApplicationResponseExecution, new_id
from nexuspilot_api.services.application_tools import authorized_application_context
from nexuspilot_api.services.remote_tool_adapter import RemoteToolAdapter


class CallerOutcomeUnknown(InvalidRequestError):
    """A caller write may have happened; automatic replay is unsafe."""


def validate_final_schema(schema, depth=0):
    """Accept only bounded local type/property/required/items/enum schema checks."""
    if schema is None:
        return
    if (
        not isinstance(schema, dict)
        or depth > 8
        or len(json.dumps(schema, allow_nan=False)) > 16000
    ):
        raise ValueError("Invalid final output schema")
    if set(schema) - {"type", "properties", "required", "items", "enum", "additionalProperties"}:
        raise ValueError("Unsupported final output schema keyword")
    kind = schema.get("type")
    if "type" in schema and kind is None:
        raise ValueError("Schema type cannot be null")
    if kind is not None and not isinstance(kind, str):
        raise ValueError("Unsupported final output type")
    if kind not in {
        None,
        "object",
        "array",
        "string",
        "number",
        "integer",
        "boolean",
        "null",
    }:
        raise ValueError("Unsupported final output type")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict) or len(properties) > 100:
        raise ValueError("Invalid schema properties")
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
        raise ValueError("Invalid required fields")
    if len(required) != len(set(required)):
        raise ValueError("Duplicate required fields")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ValueError("Invalid enum")
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        raise ValueError("additionalProperties must be boolean")
    for value in properties.values():
        if not isinstance(value, dict):
            raise ValueError("Property schema must be an object")
        validate_final_schema(value, depth + 1)
    if "items" in schema:
        if not isinstance(schema["items"], dict):
            raise ValueError("Items schema must be an object")
        validate_final_schema(schema["items"], depth + 1)


def json_values_equal(left, right):
    """Compare JSON values without treating booleans as numbers, including nested enums."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            json_values_equal(left[k], right[k]) for k in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            json_values_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def parse_final_json(text):
    """Reject non-JSON numeric constants anywhere before applying the local Schema subset."""
    value = json.loads(text)
    json.dumps(value, allow_nan=False)
    return value


def matches_final_schema(value, schema):
    """Check the documented local subset without external schema retrieval."""
    if schema is None:
        return True
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    kind = schema.get("type")
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if kind == "integer" and isinstance(value, float) and value.is_integer():
        value = int(value)
    if kind and (
        not isinstance(value, types[kind])
        or kind in {"number", "integer"}
        and isinstance(value, bool)
    ):
        return False
    if "enum" in schema and not any(json_values_equal(value, item) for item in schema["enum"]):
        return False
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if not set(schema.get("required", [])) <= value.keys():
            return False
        if schema.get("additionalProperties") is False and set(value) - props.keys():
            return False
        return all(matches_final_schema(v, props[k]) for k, v in value.items() if k in props)
    if isinstance(value, list) and "items" in schema:
        return all(matches_final_schema(v, schema["items"]) for v in value)
    return True


def validate_platform_result(value, execution_id):
    """Reject mismatched identities and unsupported outcome combinations after dispatch."""
    if (
        not isinstance(value, dict)
        or value.get("protocol_version") != "1"
        or value.get("execution_id") != execution_id
    ):
        raise ValueError("Invalid caller result identity")
    if value.get("disposition") == "complete":
        if set(value) != {"protocol_version", "execution_id", "disposition"}:
            raise ValueError("Final acceptance cannot contain a business execution")
        return value
    if set(value) != {
        "protocol_version",
        "execution_id",
        "disposition",
        "execution_status",
        "result",
        "error",
    }:
        raise ValueError("Invalid caller result fields")
    status, disposition = value["execution_status"], value["disposition"]
    if (disposition, status) not in {
        ("continue", "succeeded"),
        ("continue", "failed"),
        ("continue", "rejected"),
        ("stop", "unknown"),
        ("stop", "partially_succeeded"),
    }:
        raise ValueError("Invalid caller disposition")
    if status == "succeeded":
        if not isinstance(value["result"], dict) or value["error"] is not None:
            raise ValueError("Invalid success result")
    else:
        error = value["error"]
        if (
            not isinstance(error, dict)
            or set(error) != {"code", "message", "retryable"}
            or type(error["retryable"]) is not bool
        ):
            raise ValueError("Invalid caller error")
        if not all(
            isinstance(error[k], str) and 0 < len(error[k]) <= 2000 for k in ("code", "message")
        ):
            raise ValueError("Invalid caller error text")
        if status in {"failed", "rejected"} and value["result"] is not None:
            raise ValueError("Failure cannot claim success data")
        if status in {"unknown", "partially_succeeded"} and error["retryable"]:
            raise ValueError("Ambiguous writes cannot be retried")
    return value


def connection_snapshot(config):
    """Pin non-secret routing/protocol fields; credentials remain private and rotatable."""
    return {
        "protocol_version": "1",
        "base_url": config.base_url.rstrip("/"),
        "path": config.response_processing_path,
        "allow_local_http": config.allow_local_http,
    }


async def require_body_connection(db, run_id, *, require_binding=False):
    """Resolve caller identity only from the persisted Run and current private configuration."""
    resolved = await authorized_application_context(db, run_id)
    if resolved is None or not resolved[2].response_processing_path:
        raise InvalidRequestError("Caller response processing is not approved for this Run")
    if require_binding:
        binding = await db.get(ApplicationResponseBinding, run_id)
        if (
            binding is None
            or binding.mapping_id != resolved[1].mapping_id
            or binding.connection_json != connection_snapshot(resolved[2])
        ):
            raise InvalidRequestError("Caller connection changed or creation binding is missing")
    return resolved


async def process_application_body(service, workflow, node, response, timeout_seconds):
    """Dispatch once; restore saved results, never resend an unconfirmed business write."""
    db, storage = (
        service.model_invocation_service.db_session,
        service.model_invocation_service.storage,
    )
    _, mapping, config = await require_body_connection(db, workflow.run_id, require_binding=True)
    if response.finish_reason != "stop" or not response.output_text:
        raise InvalidRequestError("Incomplete caller response cannot be executed")
    destination = config.base_url.rstrip("/") + config.response_processing_path
    previous_destination = await db.scalar(
        select(ApplicationResponseExecution.destination)
        .where(ApplicationResponseExecution.run_id == workflow.run_id)
        .limit(1)
    )
    if previous_destination is not None and previous_destination != destination:
        raise InvalidRequestError("Caller processing destination changed during this Run")
    record = await db.scalar(
        select(ApplicationResponseExecution).where(
            ApplicationResponseExecution.response_id == response.id
        )
    )
    if record is None:
        identity = new_id()
        envelope = {
            "protocol_version": "1",
            "execution_id": identity,
            "application_id": mapping.application_id,
            "external_user_id": mapping.external_user_id,
            "run_id": workflow.run_id,
            "workflow_id": workflow.workflow_execution_id,
            "response_id": response.id,
            "content": response.output_text,
        }
        encoded = json.dumps(envelope, ensure_ascii=False).encode()
        if len(encoded) > 1024 * 1024:
            raise InvalidRequestError("Caller request exceeds 1 MiB")
        saved = await storage.put_bytes(
            f"application-responses/{workflow.run_id}/{identity}/request.json",
            encoded,
            "application/json",
        )
        record = ApplicationResponseExecution(
            execution_id=identity,
            run_id=workflow.run_id,
            response_id=response.id,
            request_uri=saved.uri,
            destination=destination,
            status="prepared",
        )
        db.add(record)
        await db.commit()
    if record.run_id != workflow.run_id or record.destination != destination:
        raise InvalidRequestError("Caller processing configuration changed")
    if record.status == "completed" and record.result_uri:
        obj = await storage.open_object(record.result_uri)
        return validate_platform_result(
            json.loads(await asyncio.to_thread(b"".join, obj.chunks)), record.execution_id
        )
    if record.status != "prepared":
        if record.status == "dispatching":
            record.status = "unknown"
            await db.commit()
        raise CallerOutcomeUnknown("Caller execution outcome unknown; automatic replay refused")
    obj = await storage.open_object(record.request_uri)
    envelope = json.loads(await asyncio.to_thread(b"".join, obj.chunks))
    await service.workflow_state.ensure_workflow_running(workflow)
    changed = await db.execute(
        update(ApplicationResponseExecution)
        .where(
            ApplicationResponseExecution.execution_id == record.execution_id,
            ApplicationResponseExecution.status == "prepared",
        )
        .values(status="dispatching")
    )
    await db.commit()
    if changed.rowcount != 1:
        raise InvalidRequestError("Caller execution already dispatched")
    try:
        adapter = RemoteToolAdapter(
            base_url=config.base_url,
            token=config.service_token.get_secret_value(),
            allow_local_http=config.allow_local_http,
        )
        await service.workflow_state.append_event(
            workflow,
            node=node,
            event_type="application.processing.started",
            public_summary="Waiting for application processing",
            public_payload={"execution_id": record.execution_id, "response_id": response.id},
        )
        pending = asyncio.create_task(
            adapter.execute_body(
                path=config.response_processing_path,
                envelope=envelope,
                timeout_seconds=timeout_seconds,
            )
        )
        try:
            while not pending.done():
                await asyncio.wait({pending}, timeout=0.2)
                await service.workflow_state.ensure_workflow_running(workflow)
                # Release the Worker ownership lock so cancel/lease renewal can proceed.
                await db.commit()
            value = await pending
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        value = validate_platform_result(value, record.execution_id)
        saved = await storage.put_bytes(
            f"application-responses/{workflow.run_id}/{record.execution_id}/result.json",
            json.dumps(value, ensure_ascii=False).encode(),
            "application/json",
        )
        record.result_uri, record.status = saved.uri, "completed"
        await db.commit()
        await service.workflow_state.append_event(
            workflow,
            node=node,
            event_type="application.processing.completed",
            public_summary="Application processing recorded",
            public_payload={
                "execution_id": record.execution_id,
                "disposition": value["disposition"],
                "execution_status": value.get("execution_status"),
            },
        )
        return value
    except (httpx.HTTPError, TimeoutError, ValueError, ObjectStorageError) as error:
        record.status = "unknown"
        await db.commit()
        raise CallerOutcomeUnknown(
            "Caller execution outcome unknown; automatic replay refused"
        ) from error
