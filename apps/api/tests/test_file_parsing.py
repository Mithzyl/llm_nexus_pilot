"""Contract tests for provider-independent extraction, provenance and bounded reading."""

import hashlib
import os
import sys
from pathlib import Path

import pytest

from nexuspilot_api.schemas.file_parsing import FileParseRequest, ParsedFileDocument
from nexuspilot_api.services.file_parsing_service import (
    FileParsingError,
    get_file_outline,
    parse_file_content,
    read_file_content,
    read_file_table,
    search_file_content,
)


def parse_fixture(content: bytes, media_type="text/plain") -> ParsedFileDocument:
    """Parse a bounded fixture with the same identity checks used for stored artifacts."""

    return parse_file_content(
        FileParseRequest(
            source_id="00000000-0000-0000-0000-000000000001",
            source_hash=hashlib.sha256(content).hexdigest(),
            filename="fixture",
            media_type=media_type,
        ),
        content,
    )


def test_text_preserves_unicode_line_locations_and_complete_pagination():
    """Every original line remains reachable, including blanks and final newline."""

    document = parse_fixture("标题\n\n你好\n".encode())
    assert document.status == "complete"
    first = read_file_content(document, offset=0, limit=2)
    second = read_file_content(document, offset=first.next_offset, limit=2)
    assert first.has_more and not second.has_more
    assert "".join(block.text for block in first.blocks + second.blocks) == "标题\n\n你好\n"
    assert [block.line_start for block in document.blocks] == [1, 2, 3]
    assert read_file_content(document, offset=3).blocks == []
    assert get_file_outline(document)["block_count"] == 3
    assert search_file_content(document, "你好").blocks[0].line_start == 3
    assert not search_file_content(document, "absent").blocks


def test_csv_preserves_cells_multiline_records_and_formula_as_text():
    """CSV retains row/column positions without executing formulas or guessing headers."""

    document = parse_fixture(b'name,value\n"a\nb",=SUM(A1:A2)\n', "text/csv")
    table = read_file_table(document, offset=1, limit=1)
    assert table.blocks[0].cells == ["a\nb", "=SUM(A1:A2)"]
    assert table.blocks[0].row_number == 2
    assert (table.blocks[0].line_start, table.blocks[0].line_end) == (2, 3)
    assert table.header_inferred is False


def test_json_preserves_original_numbers_and_rejects_duplicate_keys():
    """Validation never rounds numeric source text or silently drops duplicate keys."""

    source = b'{"amount":1234567890123456789.123456789}\n'
    assert "".join(b.text for b in parse_fixture(source, "application/json").blocks) == (
        source.decode()
    )
    for invalid in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"broken":'):
        with pytest.raises(FileParsingError):
            parse_fixture(invalid, "application/json")


@pytest.mark.parametrize("content", [b"", b" \n\t", b"\xff", b"a\x00b"])
def test_empty_or_invalid_text_never_becomes_successful_content(content):
    """Empty, undecodable and binary-disguised inputs are explicit failures."""

    with pytest.raises(FileParsingError):
        parse_fixture(content)


def test_limits_and_hash_mismatch_fail_without_partial_success():
    """File identity and bounded parse/read operations cannot be bypassed."""

    content = b"line\n"
    request = FileParseRequest(
        source_id="00000000-0000-0000-0000-000000000001",
        source_hash="0" * 64,
        filename="fixture",
        media_type="text/plain",
    )
    with pytest.raises(FileParsingError, match="hash"):
        parse_file_content(request, content)
    with pytest.raises(FileParsingError):
        parse_fixture(b"x" * (1024 * 1024 + 1))
    with pytest.raises(FileParsingError):
        parse_fixture(b"a\n" * 10001)
    document = parse_fixture(content)
    for offset, limit in [(-1, 1), (0, 0), (0, 101), (99, 1)]:
        with pytest.raises(ValueError):
            read_file_content(document, offset=offset, limit=limit)
    with pytest.raises(ValueError):
        search_file_content(document, "")
    for filename in ("../escape", "bad\x00name", "bad\ud800name"):
        with pytest.raises(ValueError):
            FileParseRequest.model_validate(request.model_dump() | {"filename": filename})


def test_unsupported_images_and_forged_office_pdf_fail_explicitly():
    """Unimplemented formats and signatures never silently fall back to native providers."""

    with pytest.raises(FileParsingError, match="unsupported"):
        parse_fixture(b"example", "image/png")
    with pytest.raises(FileParsingError, match="signature"):
        parse_fixture(
            b"example", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    with pytest.raises(FileParsingError, match="signature"):
        parse_fixture(b"not a PDF", "application/pdf")


def test_pdf_partial_coverage_and_page_locations(monkeypatch):
    """An unextractable page is recorded separately and never claimed as fully read."""

    monkeypatch.setattr(
        "nexuspilot_api.services.file_parsing_service.extract_pdf_pages",
        lambda content: (["Page one\n", "", "Page three\n"], "fixture-poppler"),
    )
    document = parse_fixture(b"%PDF-1.7\nfixture", "application/pdf")
    assert document.status == "partial"
    assert document.total_pages == 3
    assert document.uncovered_pages == [2]
    assert [block.page_number for block in document.blocks] == [1, 3]
    assert document.warnings


def test_pdf_sandbox_unavailable_does_not_run_unrestricted(monkeypatch):
    """Missing isolation is a failure, never a reason to parse on the host directly."""

    monkeypatch.setattr(
        "nexuspilot_api.infrastructure.pdf_text_extraction.shutil.which", lambda _: None
    )
    with pytest.raises(FileParsingError, match="unavailable"):
        parse_fixture(b"%PDF-1.7\nfixture", "application/pdf")


def test_pdf_output_reader_rejects_links_and_oversized_results(tmp_path):
    """Parser-controlled paths cannot make the parent read outside files or oversized output."""

    from nexuspilot_api.infrastructure.pdf_text_extraction import (
        PdfExtractionError,
        _read_output_text,
    )

    original = tmp_path / "original.txt"
    original.write_text("fixture outside parser result", encoding="utf-8")
    symbolic = tmp_path / "symbolic.txt"
    symbolic.symlink_to(original)
    hard_link = tmp_path / "hard.txt"
    os.link(original, hard_link)
    for path in (symbolic, hard_link):
        with pytest.raises(PdfExtractionError):
            _read_output_text(path, maximum_bytes=100)
    plain = tmp_path / "plain.txt"
    plain.write_text("content", encoding="utf-8")
    with pytest.raises(PdfExtractionError):
        _read_output_text(plain, maximum_bytes=3)
    assert _read_output_text(plain, maximum_bytes=100) == "content"


def test_pdf_metadata_capture_never_opens_parser_controlled_output_paths(tmp_path, monkeypatch):
    """A parser-created output symlink must not make the parent truncate an outside file."""

    from nexuspilot_api.infrastructure import pdf_text_extraction as extractor

    marker = tmp_path / "outside.txt"
    marker.write_text("unchanged", encoding="utf-8")
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "command-output").symlink_to(marker)
    monkeypatch.setattr(extractor, "_sandbox_command", lambda directory, command: command)

    class FinishedFixtureProcess:
        """Return fixed metadata using only the provided descriptor; never run a command."""

        returncode = 0

        def __init__(self, *args, **kwargs):
            """Write a harmless metadata response to simulate a completed parser child."""
            kwargs["stdout"].write(b"Pages: 1\n")
            kwargs["stdout"].flush()

        def poll(self):
            """Report a completed process without OS interaction."""
            return 0

        def wait(self, **kwargs):
            """Return the completed fixture status."""
            return 0

    monkeypatch.setattr(extractor.subprocess, "Popen", FinishedFixtureProcess)
    try:
        assert extractor._run_pdf_command(workspace, ["fixture"]) == "Pages: 1\n"
    finally:
        assert marker.read_text(encoding="utf-8") == "unchanged"


def build_text_pdf_fixture() -> bytes:
    """Build a minimal, real one-page PDF with offsets calculated from its bytes."""

    stream = b"BT /F1 12 Tf 40 120 Td (Unified file parsing) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream",
    ]
    content = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        content.extend(f"{offset:010} 00000 n \n".encode())
    content.extend(
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(content)


@pytest.mark.infrastructure
@pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_PDF_SANDBOX") != "1", reason="explicit PDF sandbox opt-in"
)
def test_real_pdf_extraction_uses_os_sandbox():
    """Exercise installed Poppler and OS isolation without a model or external service."""

    document = parse_fixture(build_text_pdf_fixture(), "application/pdf")
    assert document.total_pages == 1
    assert document.status == "complete"
    assert "Unified file parsing" in "".join(b.text for b in document.blocks)
    assert "poppler-sha256:" in document.parser_version


@pytest.mark.infrastructure
@pytest.mark.skipif(
    os.environ.get("NEXUSPILOT_TEST_PDF_SANDBOX") != "1", reason="explicit PDF sandbox opt-in"
)
def test_real_pdf_sandbox_denies_unbound_file_and_network(tmp_path):
    """Use harmless probes to confirm isolation, not just successful document extraction."""

    from nexuspilot_api.infrastructure.pdf_text_extraction import _run_pdf_command

    workspace = tmp_path / "allowed"
    workspace.mkdir()
    forbidden = tmp_path / "outside.txt"
    forbidden.write_text("fixture only", encoding="utf-8")
    probe = (
        "import errno,socket,sys\n"
        "try: open(sys.argv[1]).read()\n"
        "except OSError as exc: assert exc.errno in (errno.EPERM,errno.EACCES,errno.ENOENT)\n"
        "else: raise AssertionError('unbound file was readable')\n"
        "try:\n"
        " s=socket.socket();s.connect(('127.0.0.1',9))\n"
        "except OSError as exc:\n"
        " assert exc.errno in (errno.EPERM,errno.EACCES,errno.ENETUNREACH,errno.ECONNREFUSED)\n"
        "else: raise AssertionError('unexpected network connection')\n"
        "print('file denied')\n"
    )
    # On macOS network denial must be a permission error, not merely a closed port.
    if sys.platform == "darwin":
        probe = probe.replace(
            "errno.EPERM,errno.EACCES,errno.ENETUNREACH,errno.ECONNREFUSED",
            "errno.EPERM,errno.EACCES",
        )
    elif sys.platform == "linux":
        host_network = os.stat("/proc/self/ns/net").st_ino
        probe = f"import os\nassert os.stat('/proc/self/ns/net').st_ino != {host_network}\n" + probe
    output = _run_pdf_command(
        workspace.resolve(),
        [str(Path(sys.executable).resolve()), "-I", "-c", probe, str(forbidden)],
    )
    assert output.strip() == "file denied"
