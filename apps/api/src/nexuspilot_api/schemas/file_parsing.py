"""Provider-neutral file extraction and bounded reading contracts, independent of uploads."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    page_number: int | None = Field(default=None, ge=1)
    row_number: int | None = Field(default=None, ge=1)
    cells: list[str] | None = None


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


class FileContentPage(BaseModel):
    """Return exact blocks and explicit continuation, not a summary of unreturned content."""

    model_config = ConfigDict(extra="forbid")
    blocks: list[ParsedFileBlock]
    total_blocks: int
    next_offset: int | None
    has_more: bool
    header_inferred: Literal[False] = False


class ParsedArtifactReference(BaseModel):
    """Select blocks from a Run-owned Artifact; no URL, path or identity overrides are allowed."""

    model_config = ConfigDict(extra="forbid")
    artifact_id: str = Field(min_length=36, max_length=36)
    block_offset: int = Field(default=0, ge=0, le=10000)
    block_limit: int | None = Field(default=None, ge=1, le=100)
