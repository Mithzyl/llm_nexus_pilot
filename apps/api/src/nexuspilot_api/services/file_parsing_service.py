"""Extract immutable text/table evidence and expose bounded, provider-neutral reading helpers.

These helpers do not authorize files or execute model tool calls. The artifact
boundary validates ownership; stage 7 must authorize any future tool exposure.
"""

import csv
import hashlib
import io
import json

from pydantic import ValidationError

from nexuspilot_api.infrastructure.office_text_extraction import (
    OFFICE_MEDIA_TYPES,
    OfficeExtractionError,
    extract_office_content,
)
from nexuspilot_api.infrastructure.pdf_text_extraction import PdfExtractionError, extract_pdf_pages
from nexuspilot_api.schemas.file_parsing import (
    FileContentPage,
    FileParserDescriptor,
    FileParseRequest,
    ParsedFileBlock,
    ParsedFileDocument,
)

MAX_PARSE_BYTES = 1024 * 1024
MAX_PARSE_BLOCKS = 10000
MAX_READ_BYTES = 128 * 1024
TEXT_MEDIA_TYPES = (
    "text/plain",
    "text/markdown",
    "text/x-python",
    "text/javascript",
    "application/javascript",
    "text/css",
    "application/xml",
    "text/xml",
    "application/yaml",
    "text/yaml",
)
FILE_PARSERS = (
    FileParserDescriptor(
        name="office_document",
        version="1",
        media_types=OFFICE_MEDIA_TYPES,
        requires_sandbox=True,
        supports_tables=True,
    ),
    FileParserDescriptor(name="utf8_text", version="1", media_types=TEXT_MEDIA_TYPES),
    FileParserDescriptor(name="json_text", version="1", media_types=("application/json",)),
    FileParserDescriptor(
        name="delimited_table",
        version="1",
        media_types=("text/csv", "text/tab-separated-values"),
        supports_tables=True,
    ),
    FileParserDescriptor(
        name="pdf_embedded_text",
        version="1",
        media_types=("application/pdf",),
        requires_sandbox=True,
    ),
)


class FileParsingError(ValueError):
    """Report safe extraction failures without source text, paths or storage credentials."""


def _validate_json_text(content: str) -> None:
    """Validate bounded JSON without duplicate keys, nonfinite numbers or silent coercion."""

    depth, in_string, escaped = 0, False, False
    for character in content:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > 64:
                raise FileParsingError("JSON nesting exceeds the allowed depth")
        elif character in "]}":
            depth -= 1

    def reject_duplicate_keys(pairs):
        """Reject ambiguous JSON objects instead of silently keeping the last property."""

        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            raise FileParsingError("JSON contains duplicate keys")
        return dict(pairs)

    def reject_nonfinite_number(number):
        """Reject nonstandard NaN and infinity literals without including user data in errors."""

        raise FileParsingError("JSON contains a nonfinite number")

    try:
        json.loads(
            content,
            object_pairs_hook=reject_duplicate_keys,
            parse_int=str,
            parse_float=str,
            parse_constant=reject_nonfinite_number,
        )
    except (ValueError, RecursionError) as exc:
        raise FileParsingError("Invalid or ambiguous JSON document") from exc


def parse_file_content(request: FileParseRequest, content: bytes) -> ParsedFileDocument:
    """Extract verified bytes into bounded blocks; no model, storage or arbitrary network calls.

    PDF launches the dedicated OS-isolated Poppler adapter; Office calls the
    configured executor, which runs a disposable offline Linux container.
    Unsupported, empty, corrupt or over-limit inputs raise FileParsingError;
    PDF pages with no embedded text produce explicit partial coverage.
    """

    if not 0 < len(content) <= MAX_PARSE_BYTES:
        raise FileParsingError("File size exceeds the supported parsing range")
    if hashlib.sha256(content).hexdigest() != request.source_hash:
        raise FileParsingError("File content hash does not match source")
    media_type = request.media_type.split(";", 1)[0].strip().lower()
    parser = next((p for p in FILE_PARSERS if media_type in p.media_types), None)
    if parser is None:
        raise FileParsingError("File format is unsupported by installed parsers")
    blocks: list[ParsedFileBlock] = []
    uncovered_pages: list[int] = []
    warnings: list[str] = []
    total_pages = None
    parser_version = parser.version
    if media_type in OFFICE_MEDIA_TYPES:
        if not content.startswith(b"PK\x03\x04"):
            raise FileParsingError("Office signature does not match declared type")
        try:
            extracted = extract_office_content(content, media_type)
            if (
                extracted.get("protocol") != "office_parse.v1"
                or extracted.get("source_hash") != request.source_hash
                or extracted.get("media_type") != media_type
                or extracted.get("status") not in {"complete", "partial"}
                or not isinstance(extracted.get("parser_version"), str)
            ):
                raise FileParsingError("Office executor result does not match source")
            blocks = [ParsedFileBlock.model_validate(block) for block in extracted["blocks"]]
            warnings = extracted["warnings"]
            if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
                raise FileParsingError("Office executor warnings are invalid")
            parser_version = extracted["parser_version"]
            if len({block.block_id for block in blocks}) != len(blocks):
                raise FileParsingError("Office executor returned duplicate block identities")
        except OfficeExtractionError as exc:
            raise FileParsingError(str(exc)) from exc
        except (ValidationError, KeyError, TypeError) as exc:
            raise FileParsingError("Office executor returned invalid structured content") from exc
    elif media_type == "application/pdf":
        if not content.startswith(b"%PDF-"):
            raise FileParsingError("PDF signature does not match declared type")
        try:
            pages, engine_version = extract_pdf_pages(content)
        except PdfExtractionError as exc:
            raise FileParsingError(str(exc)) from exc
        parser_version = f"{parser.version}/{engine_version}"
        total_pages = len(pages)
        warnings.append("Embedded text only; graphics and table structure are not interpreted")
        for page_number, page in enumerate(pages, 1):
            if not page.strip():
                uncovered_pages.append(page_number)
            else:
                blocks.extend(_text_blocks(page, page_number=page_number))
        if uncovered_pages:
            warnings.append("Some pages contain no extracted text; OCR is not enabled")
    else:
        if content.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG")):
            raise FileParsingError("Binary signature conflicts with declared text format")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise FileParsingError("File is not valid UTF-8 text") from exc
        if not text.strip() or any(ord(c) < 32 and c not in "\t\r\n" for c in text):
            raise FileParsingError(
                "File has no readable text or contains binary control characters"
            )
        if parser.name == "json_text":
            _validate_json_text(text)
        if parser.name == "delimited_table":
            delimiter = "\t" if media_type == "text/tab-separated-values" else ","
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
            previous_line = 0
            try:
                for row_number, cells in enumerate(reader, 1):
                    if row_number > MAX_PARSE_BLOCKS or len(cells) > 1024:
                        raise FileParsingError(
                            "Table row or column count exceeds the parsing limit"
                        )
                    blocks.append(
                        ParsedFileBlock(
                            block_id=f"row:{row_number}",
                            kind="table_row",
                            text=json.dumps(cells, ensure_ascii=False),
                            cells=cells,
                            line_start=previous_line + 1,
                            line_end=reader.line_num,
                            row_number=row_number,
                        )
                    )
                    previous_line = reader.line_num
            except csv.Error as exc:
                raise FileParsingError("Invalid or oversized delimited table") from exc
            warnings.append(
                "First row is preserved as data; headers and cell types are not inferred"
            )
        else:
            blocks = _text_blocks(text)
    if not blocks:
        raise FileParsingError("No text was extracted; OCR may be required")
    if len(blocks) > MAX_PARSE_BLOCKS:
        raise FileParsingError("Extracted block count exceeds the parsing limit")
    if any(len(b.text.encode()) > MAX_READ_BYTES for b in blocks):
        raise FileParsingError("A single line or table row exceeds the reading size limit")
    return ParsedFileDocument(
        source_id=request.source_id,
        source_hash=request.source_hash,
        filename=request.filename,
        media_type=media_type,
        parser_name=parser.name,
        parser_version=parser_version,
        status=(
            extracted["status"]
            if media_type in OFFICE_MEDIA_TYPES
            else "partial"
            if uncovered_pages
            else "complete"
        ),
        blocks=blocks,
        total_pages=total_pages,
        uncovered_pages=uncovered_pages,
        warnings=warnings,
    )


def _text_blocks(text: str, *, page_number: int | None = None) -> list[ParsedFileBlock]:
    """Preserve line endings and source-relative locations without rendering or executing text."""

    lines = text.splitlines(keepends=True)
    if len(lines) > MAX_PARSE_BLOCKS:
        raise FileParsingError("Extracted block count exceeds the parsing limit")
    return [
        ParsedFileBlock(
            block_id=f"page:{page_number}:line:{line}" if page_number else f"line:{line}",
            text=content,
            line_start=line,
            line_end=line,
            page_number=page_number,
        )
        for line, content in enumerate(lines, 1)
    ]


def _read_blocks(blocks: list[ParsedFileBlock], offset: int, limit: int) -> FileContentPage:
    """Bound page count and bytes, always returning a continuation for unreturned blocks."""

    if not 0 <= offset <= len(blocks) or not 1 <= limit <= 100:
        raise ValueError("Invalid file content range")
    selected: list[ParsedFileBlock] = []
    byte_count = 0
    for block in blocks[offset : offset + limit]:
        size = len(block.model_dump_json().encode())
        if byte_count + size > MAX_READ_BYTES:
            if not selected:
                raise ValueError("Block exceeds the reading page size")
            break
        selected.append(block)
        byte_count += size
    next_offset = offset + len(selected)
    has_more = next_offset < len(blocks)
    return FileContentPage(
        blocks=selected,
        total_blocks=len(blocks),
        next_offset=next_offset if has_more else None,
        has_more=has_more,
    )


def read_file_content(document: ParsedFileDocument, *, offset=0, limit=100) -> FileContentPage:
    """Read a bounded page of the pinned document; offsets refer to blocks, not bytes."""

    return _read_blocks(document.blocks, offset, limit)


def read_file_table(document: ParsedFileDocument, *, offset=0, limit=100) -> FileContentPage:
    """Read delimited table rows with original cell strings and no header or formula inference."""

    rows = [block for block in document.blocks if block.kind == "table_row"]
    if not rows:
        raise ValueError("Document has no structured table")
    return _read_blocks(rows, offset, limit)


def search_file_content(document: ParsedFileDocument, query: str, *, offset=0, limit=100):
    """Search literal text in one fixed document; no regex, SQL, ranking or external retrieval."""

    if not query.strip() or len(query) > 256:
        raise ValueError("File search requires a nonempty query of at most 256 characters")
    return _read_blocks([b for b in document.blocks if query in b.text], offset, limit)


def get_file_outline(document: ParsedFileDocument) -> dict:
    """Return structural facts and coverage without inventing headings or semantic summaries."""

    return {
        "filename": document.filename,
        "block_count": len(document.blocks),
        "total_pages": document.total_pages,
        "uncovered_pages": document.uncovered_pages,
        "table_rows": sum(b.kind == "table_row" for b in document.blocks),
        "status": document.status,
        "warnings": document.warnings,
        "headings": [
            {"block_id": block.block_id, "level": block.heading_level, "text": block.text}
            for block in document.blocks
            if block.heading_level is not None
        ],
        "sheets": list(dict.fromkeys(b.sheet_name for b in document.blocks if b.sheet_name)),
        "slides": list(dict.fromkeys(b.slide_number for b in document.blocks if b.slide_number)),
    }
