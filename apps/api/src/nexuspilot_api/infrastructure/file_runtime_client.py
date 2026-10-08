"""Bounded local transport to the fixed file parser; never execute code in the API."""

import base64
import hashlib
import json
import socket
import time

from nexuspilot_api.core.config import get_settings

MAX_RESULT_BYTES = 4 * 1024 * 1024


class FileRuntimeError(ValueError):
    """Expose safe executor failures without source text, paths or subprocess errors."""


def request_file_parse(content: bytes, media_type: str, *, protocol: str) -> dict:
    """Send a bounded file request and validate response identity before format adaptation."""

    socket_path = get_settings().file_runtime_socket
    if not socket_path:
        raise FileRuntimeError("File executor is not configured")
    if not 0 < len(content) <= 1024 * 1024:
        raise FileRuntimeError("Unsupported file request size")
    request = json.dumps({
        "protocol": protocol, "media_type": media_type,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }).encode() + b"\n"
    try:
        deadline = time.monotonic() + 125
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(10)
            connection.connect(socket_path)
            connection.sendall(request)
            received = bytearray()
            while not received.endswith(b"\n"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                connection.settimeout(remaining)
                chunk = connection.recv(65536)
                if not chunk:
                    raise FileRuntimeError("File executor returned an incomplete response")
                received.extend(chunk)
                if len(received) > MAX_RESULT_BYTES + 512:
                    raise FileRuntimeError("File executor returned oversized content")
        report = json.loads(received)
        if not isinstance(report, dict):
            raise FileRuntimeError("File executor returned invalid content")
        errors = {
            "file_executor_busy": "File executor is busy; retry later",
            "office_parse_failed": "Office document is corrupt, unsupported or exceeds limits",
            "file_parse_failed": "Document is corrupt, encrypted, unsupported or exceeds limits",
            "file_executor_failed": "File executor failed; no host fallback was attempted",
        }
        if "error" in report:
            code = report["error"]
            raise FileRuntimeError(errors.get(code, "File executor failed")
                                   if isinstance(code, str) else "File executor failed")
        if (
            report.get("protocol") != protocol
            or report.get("media_type") != media_type
            or report.get("source_hash") != hashlib.sha256(content).hexdigest()
            or not isinstance(report.get("parser_version"), str)
            or not 0 < len(report["parser_version"]) <= 2048
        ):
            raise FileRuntimeError("File executor result does not match source or protocol")
        return report
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, FileRuntimeError):
            raise
        raise FileRuntimeError("File executor unavailable or returned invalid content") from exc
