"""Perform one bounded read-only HTTP operation selected by trusted static registration."""

import asyncio
import json
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from nexuspilot_api.services.agent_file_tool_service import ToolOperationError


class RemoteToolAdapter:
    """Keep credentials outside model arguments and never retry an ambiguous HTTP operation."""

    def __init__(self, *, base_url, token, allow_local_http=False):
        """Validate service configuration; HTTP is only for explicit loopback tests."""
        parsed = urlsplit(base_url)
        local_http = (
            allow_local_http
            and parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        )
        if (
            parsed.scheme != "https"
            and not local_http
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not token
            or any(c in token for c in "\r\n")
        ):
            raise ValueError("Invalid remote service configuration")
        self.base_url = base_url.rstrip("/")
        self._token = token

    async def execute(
        self,
        *,
        path,
        execution_id,
        application_id,
        external_user_id,
        tool_name,
        version,
        arguments,
        output,
        timeout_seconds,
        max_result_bytes,
    ):
        """Send one authenticated envelope and return schema-validated JSON or a redacted error.

        The caller supplies identity from the persisted Run, never from model parameters.
        Redirects are not followed. Cancellation propagates; no response means no claimed success.
        """
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path
            or any(c in path for c in "?#\\\r\n")
        ):
            raise ValueError("Invalid registered operation path")
        envelope = {
            "tool_execution_id": execution_id,
            "application_id": application_id,
            "external_user_id": external_user_id,
            "tool_name": tool_name,
            "tool_version": version,
            "arguments": arguments.model_dump(mode="json"),
        }
        try:
            async with asyncio.timeout(timeout_seconds):
                async with httpx.AsyncClient(
                    follow_redirects=False, trust_env=False, timeout=timeout_seconds
                ) as client:
                    async with client.stream(
                        "POST",
                        self.base_url + path,
                        headers={"Authorization": "Bearer " + self._token},
                        json=envelope,
                    ) as response:
                        if response.status_code in {401, 403}:
                            raise ToolOperationError(
                                "remote_access_denied", status="permission_denied"
                            )
                        if response.status_code == 404:
                            raise ToolOperationError("remote_resource_not_found")
                        if 300 <= response.status_code < 400:
                            raise ToolOperationError("remote_redirect_rejected")
                        if 400 <= response.status_code < 500 and response.status_code != 429:
                            raise ToolOperationError("remote_request_rejected")
                        if not 200 <= response.status_code < 300:
                            raise ToolOperationError("remote_service_unavailable")
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > max_result_bytes:
                                raise ToolOperationError("remote_result_too_large")
                            content.extend(chunk)
                        # Reject accidental credential echoes before persistence or model delivery.
                        if self._token.encode() in content:
                            raise ToolOperationError("remote_invalid_response")
                        return output.model_validate(json.loads(content)).model_dump(mode="json")
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ToolOperationError("remote_timeout", status="timed_out") from error
        except httpx.RequestError as error:
            raise ToolOperationError("remote_transport_error") from error
        except (ValidationError, ValueError, UnicodeError) as error:
            raise ToolOperationError("remote_invalid_response") from error

    async def execute_body(self, *, path, envelope, timeout_seconds):
        """Send approved caller text once; every post-dispatch error remains ambiguous to caller."""
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path
            or any(c in path for c in "?#\\\r\n")
        ):
            raise ValueError("Invalid caller path")
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(
                follow_redirects=False, trust_env=False, timeout=timeout_seconds
            ) as client:
                async with client.stream(
                    "POST",
                    self.base_url + path,
                    headers={"Authorization": "Bearer " + self._token},
                    json=envelope,
                ) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 1024 * 1024:
                            raise ValueError("Caller result exceeds 1 MiB")
                    if self._token.encode() in content:
                        raise ValueError("Caller result contains credential")
                    return json.loads(content)
