"""Verify Office result conversion without requiring local Office libraries or Docker."""

import hashlib

import pytest

from nexuspilot_api.schemas.file_parsing import FileParseRequest
from nexuspilot_api.services.file_parsing_service import FileParsingError, parse_file_content

OFFICE_TYPES = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
)


@pytest.mark.parametrize("media_type", OFFICE_TYPES)
def test_office_converts_isolated_results_without_fabricating_line_numbers(monkeypatch, media_type):
    """Keep Office source positions and explicit omissions in provider-independent results."""

    content = b"PK\x03\x04fixture"
    request = FileParseRequest(
        source_id="00000000-0000-0000-0000-000000000001",
        source_hash=hashlib.sha256(content).hexdigest(),
        filename="document",
        media_type=media_type,
    )

    def extract(source, declared_type):
        """Supply the isolated executor's validated protocol result, not a library mock."""

        assert source == content and declared_type == media_type
        return {
            "protocol": "office_parse.v1",
            "source_hash": request.source_hash,
            "media_type": media_type,
            "parser_version": "office-v1/test-image",
            "status": "partial",
            "warnings": ["Images were not extracted"],
            "blocks": [{"block_id": "paragraph:1", "text": "中文内容", "paragraph_number": 1}],
        }

    monkeypatch.setattr(
        "nexuspilot_api.services.file_parsing_service.extract_office_content", extract
    )
    document = parse_file_content(request, content)
    assert document.blocks[0].paragraph_number == 1
    assert document.blocks[0].line_start is None
    assert document.status == "partial"
    assert document.parser_name == "office_document"


def test_office_requires_configured_executor_without_host_fallback(monkeypatch):
    """Missing executor configuration must not cause host imports or pretend successful parsing."""

    from nexuspilot_api.core.config import get_settings

    monkeypatch.setattr(get_settings(), "file_runtime_socket", None)
    content = b"PK\x03\x04fixture"
    request = FileParseRequest(
        source_id="00000000-0000-0000-0000-000000000001",
        source_hash=hashlib.sha256(content).hexdigest(),
        filename="document.xlsx",
        media_type=OFFICE_TYPES[1],
    )
    with pytest.raises(FileParsingError, match="executor.*configured"):
        parse_file_content(request, content)
