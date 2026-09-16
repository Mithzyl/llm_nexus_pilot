"""Provider-neutral file extraction and bounded reading contracts, independent of uploads."""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FileParseRequest(BaseModel):
    """Pin a trusted source and declared type; ownership is resolved by the calling service."""

    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(min_length=36, max_length=36)
    source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)

    @field_validator("filename")
    @classmethod
    def validate_display_filename(cls, filename: str) -> str:
        """Reject paths and control characters; filenames are labels, never executable paths."""

        if filename in {".", ".."} or any(ord(c) < 32 or c in "/\\" for c in filename):
            raise ValueError("File name must be a safe display label")
        try:
            filename.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("File name must contain valid Unicode") from exc
        return filename


class FileParserDescriptor(BaseModel):
    """Describe installed extraction behavior without implying deployment readiness."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    version: str
    media_types: tuple[str, ...]
    requires_sandbox: bool = False
    supports_ocr: bool = False
    supports_tables: bool = False


class ParsedFileBlock(BaseModel):
    """Keep stable source positions and extracted content; tables never execute formulas."""

    model_config = ConfigDict(extra="forbid")
    block_id: str
    kind: Literal["text", "table_row"] = "text"
    text: str
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    page_number: int | None = Field(default=None, ge=1)
    row_number: int | None = Field(default=None, ge=1)
    cells: list[str] | None = None
    paragraph_number: int | None = Field(default=None, ge=1)
    heading_level: int | None = Field(default=None, ge=1, le=9)
    table_number: int | None = Field(default=None, ge=1)
    sheet_name: str | None = None
    cell_range: str | None = None
    cell_formulas: list[str | None] | None = None
    cell_types: list[str] | None = None
    cell_number_formats: list[str] | None = None
    missing_formula_values: list[str] | None = None
    merged_ranges: list[str] | None = None
    hidden: bool | None = None
    slide_number: int | None = Field(default=None, ge=1)
    shape_number: int | None = Field(default=None, ge=1)
    is_notes: bool | None = None


class ParsedWorksheetEntry(BaseModel):
    """Record every worksheet, including hidden sheets with no extracted cells."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=31)
    visibility: Literal["visible", "hidden", "veryHidden"]
    block_count: int = Field(ge=0, le=10000, strict=True)


class ParsedSlideEntry(BaseModel):
    """Record source slide order; zero blocks does not prove an empty visual slide."""

    model_config = ConfigDict(extra="forbid")
    number: int = Field(ge=1, le=10000, strict=True)
    block_count: int = Field(ge=0, le=10000, strict=True)


class ParsedWorksheetStructure(BaseModel):
    """Inventory source worksheets without claiming complete extraction of their contents."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["worksheets"]
    entries: list[ParsedWorksheetEntry] = Field(min_length=1, max_length=10000)


class ParsedSlideStructure(BaseModel):
    """Inventory source slides independently from text, tables and notes extraction."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["slides"]
    entries: list[ParsedSlideEntry] = Field(min_length=1, max_length=10000)


ParsedFileStructure = Annotated[
    ParsedWorksheetStructure | ParsedSlideStructure, Field(discriminator="kind")
]


class ParsedFileDocument(BaseModel):
    """Store immutable extraction evidence separately from the model's reading coverage."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["parsed_file.v1"] = "parsed_file.v1"
    source_id: str
    source_hash: str
    filename: str
    media_type: str
    parser_name: str
    parser_version: str
    extraction_method: Literal["embedded_text"] = "embedded_text"
    status: Literal["complete", "partial"]
    blocks: list[ParsedFileBlock]
    total_pages: int | None = None
    uncovered_pages: list[int] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    structure: ParsedFileStructure | None = None


class FileContentPage(BaseModel):
    """Return exact blocks and explicit continuation, not a summary of unreturned content."""

    model_config = ConfigDict(extra="forbid")
    blocks: list[ParsedFileBlock]
    total_blocks: int
    next_offset: int | None
    has_more: bool
    header_inferred: Literal[False] = False


def cell_range_bounds(cell_range: str) -> tuple[int, int, int, int]:
    """Validate bounded A1 coordinates and return start/end columns and rows, all one-based."""

    match = re.fullmatch(
        r"([A-Z]{1,3})([1-9][0-9]{0,6})(?::([A-Z]{1,3})([1-9][0-9]{0,6}))?", cell_range
    )
    if match is None:
        raise ValueError("Cell range must use A1 or A1:B2 coordinates without a sheet prefix")
    first_column, first_row, last_column, last_row = match.groups()
    columns = []
    for label in (first_column, last_column or first_column):
        column = 0
        for letter in label:
            column = column * 26 + ord(letter) - ord("A") + 1
        columns.append(column)
    start_row, end_row = int(first_row), int(last_row or first_row)
    if not (columns[0] <= columns[1] <= 16384 and start_row <= end_row <= 1048576):
        raise ValueError("Cell range is reversed or exceeds worksheet dimensions")
    return columns[0], start_row, columns[1], end_row


class PdfPageSelection(BaseModel):
    """Select an inclusive PDF page range, never Word or slide page guesses."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["pdf_pages"]
    page_start: int = Field(ge=1, le=10000, strict=True)
    page_end: int = Field(ge=1, le=10000, strict=True)

    @model_validator(mode="after")
    def validate_page_order(self) -> "PdfPageSelection":
        """Reject backwards ranges instead of silently swapping the requested pages."""

        if self.page_end < self.page_start:
            raise ValueError("PDF page range must be ordered")
        return self


class WordTableSelection(BaseModel):
    """Select one source table by its one-based document table number."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["word_table"]
    table_number: int = Field(ge=1, le=10000, strict=True)


class WorksheetSelection(BaseModel):
    """Select one exact worksheet name and optionally an inclusive A1 rectangle."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["worksheet"]
    sheet_name: str = Field(min_length=1, max_length=31)
    cell_range: str | None = Field(default=None, max_length=32)

    @field_validator("cell_range")
    @classmethod
    def validate_cell_range(cls, value: str | None) -> str | None:
        """Canonicalize cell addresses so equivalent requests have the same range identity."""

        if value is None:
            return None
        value = value.upper()
        cell_range_bounds(value)
        return value if ":" in value else f"{value}:{value}"


class SlideSelection(BaseModel):
    """Select an inclusive slide range, including extracted notes and table rows on those slides."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["slides"]
    slide_start: int = Field(ge=1, le=10000, strict=True)
    slide_end: int = Field(ge=1, le=10000, strict=True)

    @model_validator(mode="after")
    def validate_slide_order(self) -> "SlideSelection":
        """Reject backwards slide ranges without changing the caller's intended selection."""

        if self.slide_end < self.slide_start:
            raise ValueError("Slide range must be ordered")
        return self


ParsedFileSelection = Annotated[
    PdfPageSelection | WordTableSelection | WorksheetSelection | SlideSelection,
    Field(discriminator="kind"),
]


class ParsedArtifactReference(BaseModel):
    """Select blocks from a Run-owned Artifact; no URL, path or identity overrides are allowed."""

    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=36, max_length=36)
    block_offset: int = Field(default=0, ge=0, le=10000)
    block_limit: int | None = Field(default=None, ge=1, le=100)
    selection: ParsedFileSelection | None = None
