"""Use the local restricted executor protocol without importing Office libraries or Docker."""

import base64
import json
import socket

from nexuspilot_api.core.config import get_settings

OFFICE_MEDIA_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
)
MAX_RESULT_BYTES = 4 * 1024 * 1024


class OfficeExtractionError(ValueError):
    """Report safe unavailable, invalid or failed executor results without local path leakage."""


def extract_office_content(content: bytes, media_type: str) -> dict:
    """Send verified bytes over a local socket and bound response size and total receive time."""

    import time

    socket_path = get_settings().file_runtime_socket
    if not socket_path:
        raise OfficeExtractionError("Office executor is not configured")
    if media_type not in OFFICE_MEDIA_TYPES or not 0 < len(content) <= 1024 * 1024:
        raise OfficeExtractionError("Unsupported Office request")
    request = (
        json.dumps(
            {
                "protocol": "office_parse.v1",
                "media_type": media_type,
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        ).encode()
        + b"\n"
    )
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
                    raise OfficeExtractionError("Office executor returned an incomplete response")
                received.extend(chunk)
                if len(received) > MAX_RESULT_BYTES + 512:
                    raise OfficeExtractionError("Office executor returned oversized content")
        report = json.loads(received)
        if not isinstance(report, dict):
            raise OfficeExtractionError("Office executor returned invalid content")
        errors = {
            "file_executor_busy": "Office executor is busy; retry later",
            "office_parse_failed": "Office document is corrupt, unsupported or exceeds limits",
            "file_executor_failed": "Office executor failed; no host fallback was attempted",
        }
        if "error" in report:
            raise OfficeExtractionError(errors.get(report["error"], "Office executor failed"))
        return report
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, OfficeExtractionError):
            raise
        raise OfficeExtractionError(
            "Office executor unavailable or returned invalid content"
        ) from exc
