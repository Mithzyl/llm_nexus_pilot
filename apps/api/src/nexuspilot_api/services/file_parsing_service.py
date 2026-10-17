"""Extract immutable text/table evidence and expose bounded, provider-neutral reading helpers.

These helpers do not authorize files or execute model tool calls. The artifact
boundary validates ownership; stage 7 must authorize any future tool exposure.
"""

import csv
import hashlib
import io
import json
from collections import Counter

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
    ParsedFileSelection,
    ParsedSlideStructure,
    ParsedWorksheetStructure,
    WorksheetSelection,
    cell_range_bounds,
)

MAX_PARSE_BYTES = 20 * 1024 * 1024
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


def _parse_office_structure(extracted: dict, blocks: list[ParsedFileBlock], media_type: str):
    """Validate optional executor inventory against format, source positions and block counts."""

    raw = extracted.get("structure")
    if raw is None:
        return None  # Legacy images expose only locations for which they extracted content.
    if media_type == OFFICE_MEDIA_TYPES[1]:
        structure = ParsedWorksheetStructure.model_validate(raw)
        locations = [entry.name for entry in structure.entries]
        counts = Counter(block.sheet_name for block in blocks)
    elif media_type == OFFICE_MEDIA_TYPES[2]:
        structure = ParsedSlideStructure.model_validate(raw)
        locations = [entry.number for entry in structure.entries]
        if locations != list(range(1, len(locations) + 1)):
            raise FileParsingError("Office slide structure is not contiguous")
        counts = Counter(block.slide_number for block in blocks)
    else:
        raise FileParsingError("Office structure does not match document format")
    if (
        len(locations) != len(set(locations))
        or not set(counts).issubset(locations)
        or any(counts[location] != entry.block_count
               for location, entry in zip(locations, structure.entries, strict=True))
    ):
        raise FileParsingError("Office structure does not match extracted blocks")
    return structure


def parse_file_content(request: FileParseRequest, content: bytes) -> ParsedFileDocument:
    """Extract verified bytes into bounded blocks; no model, storage or arbitrary network calls.

    PDF launches the dedicated OS-isolated Poppler adapter; Office calls the
    configured executor, which runs a disposable offline Linux container.
    Unsupported, empty, corrupt or over-limit inputs raise FileParsingError;
    PDF pages with no embedded text produce explicit partial coverage. Optional
    Office inventories retain locations without content and are checked against blocks.
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
    structure = None
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
            structure = _parse_office_structure(extracted, blocks, media_type)
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
        structure=structure,
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


def _column_label(column: int) -> str:
    """Convert a validated one-based column number to its source A1 coordinate label."""

    label = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def _select_worksheet_cells(
    blocks: list[ParsedFileBlock], selection: WorksheetSelection
) -> list[ParsedFileBlock]:
    """Slice worksheet rows and aligned metadata without recalculation or source mutation."""

    rows = [b for b in blocks if b.kind == "table_row" and b.sheet_name == selection.sheet_name]
    if selection.cell_range is None:
        return rows
    min_column, min_row, max_column, max_row = cell_range_bounds(selection.cell_range)
    selected = []
    for row in rows:
        if row.row_number is None or not min_row <= row.row_number <= max_row:
            continue
        try:
            source_start, source_row, source_end, source_last_row = cell_range_bounds(
                row.cell_range or ""
            )
            if (
                source_row != row.row_number
                or source_last_row != source_row
                or row.cells is None
                or len(row.cells) != source_end - source_start + 1
            ):
                raise ValueError("Inconsistent row positions")
            first, last = max(source_start, min_column), min(source_end, max_column)
            if first > last:
                continue
            start, end = first - source_start, last - source_start + 1
            cells = row.cells[start:end]
            updates = {
                "cells": cells,
                "text": json.dumps(cells, ensure_ascii=False),
                "cell_range": (
                    f"{_column_label(first)}{source_row}:{_column_label(last)}{source_row}"
                ),
            }
            for name in ("cell_formulas", "cell_types", "cell_number_formats"):
                values = getattr(row, name)
                if values is not None:
                    if len(values) != len(row.cells):
                        raise ValueError("Inconsistent cell metadata")
                    updates[name] = values[start:end]
            if row.missing_formula_values is not None:
                missing = []
                for coordinate in row.missing_formula_values:
                    column, number, last_column, last_number = cell_range_bounds(coordinate)
                    if number != source_row or last_number != number or last_column != column:
                        raise ValueError("Inconsistent formula position")
                    if first <= column <= last:
                        missing.append(coordinate)
                updates["missing_formula_values"] = missing
            if row.merged_ranges is not None:
                merged = []
                for area in row.merged_ranges:
                    left, top, right, bottom = cell_range_bounds(area)
                    if left <= last and right >= first and top <= source_row <= bottom:
                        # A merge is a source fact, not a new merge clipped to our selected cells.
                        merged.append(area)
                updates["merged_ranges"] = merged
        except ValueError as exc:
            raise FileParsingError("Worksheet row metadata is invalid") from exc
        selected.append(row.model_copy(update=updates))
    return selected


def select_file_blocks(
    document: ParsedFileDocument, selection: ParsedFileSelection
) -> list[ParsedFileBlock]:
    """Match native source positions before pagination; wrong formats and empty ranges fail.

    This selects already-extracted evidence, not the parser's execution range.
    The caller retains full-document warnings and coverage in the Context snapshot.
    A complete slide inventory supplies bounds even when the last slides have no text.
    """

    expected_types = {
        "pdf_pages": "application/pdf",
        "word_table": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "worksheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "slides": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    if document.media_type != expected_types[selection.kind]:
        raise FileParsingError("Selected range does not match the document format")
    if selection.kind == "pdf_pages":
        if document.total_pages is None or selection.page_end > document.total_pages:
            raise FileParsingError("Selected PDF page range exceeds known document pages")
        selected = [
            b
            for b in document.blocks
            if b.page_number is not None
            and selection.page_start <= b.page_number <= selection.page_end
        ]
    elif selection.kind == "word_table":
        selected = [
            b
            for b in document.blocks
            if b.kind == "table_row" and b.table_number == selection.table_number
        ]
    elif selection.kind == "worksheet":
        selected = _select_worksheet_cells(document.blocks, selection)
    else:
        if (
            isinstance(document.structure, ParsedSlideStructure)
            and selection.slide_end > len(document.structure.entries)
        ):
            raise FileParsingError("Selected slide range exceeds known document slides")
        selected = [
            b
            for b in document.blocks
            if b.slide_number is not None
            and selection.slide_start <= b.slide_number <= selection.slide_end
        ]
    if not selected:
        raise FileParsingError("Selected range has no readable extracted content")
    return selected


def search_file_content(document: ParsedFileDocument, query: str, *, offset=0, limit=100):
    """Search literal text in one fixed document; no regex, SQL, ranking or external retrieval."""

    if not query.strip() or len(query) > 256:
        raise ValueError("File search requires a nonempty query of at most 256 characters")
    return _read_blocks([b for b in document.blocks if query in b.text], offset, limit)


def get_file_outline(document: ParsedFileDocument) -> dict:
    """Return all inventoried sheets/slides, or explicitly incomplete legacy block locations.

    Complete structure means a complete worksheet/slide inventory, not full text,
    images, charts, Word chapters or a PDF table of contents.
    """

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
        "structure_complete": document.structure is not None,
        "structure": document.structure.model_dump() if document.structure else None,
        "sheets": (
            [entry.name for entry in document.structure.entries]
            if isinstance(document.structure, ParsedWorksheetStructure)
            else list(dict.fromkeys(b.sheet_name for b in document.blocks if b.sheet_name))
        ),
        "slides": (
            [entry.number for entry in document.structure.entries]
            if isinstance(document.structure, ParsedSlideStructure)
            else list(dict.fromkeys(b.slide_number for b in document.blocks if b.slide_number))
        ),
    }
