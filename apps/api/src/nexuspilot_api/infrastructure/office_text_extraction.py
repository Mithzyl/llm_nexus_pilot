"""Use the restricted file executor without importing Office libraries or Docker into API."""

from nexuspilot_api.infrastructure.file_runtime_client import FileRuntimeError, request_file_parse

OFFICE_MEDIA_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
)


class OfficeExtractionError(ValueError):
    """Report safe unavailable, invalid or failed executor results without local path leakage."""


def extract_office_content(content: bytes, media_type: str) -> dict:
    """Keep the deployed Office protocol compatible while sharing bounded file transport."""

    if media_type not in OFFICE_MEDIA_TYPES:
        raise OfficeExtractionError("Unsupported Office request")
    try:
        return request_file_parse(content, media_type, protocol="office_parse.v1")
    except FileRuntimeError as exc:
        raise OfficeExtractionError(str(exc)) from exc
