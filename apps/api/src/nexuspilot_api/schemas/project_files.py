"""Public project file operations never accept storage addresses or filesystem paths."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nexuspilot_api.schemas.base import ApiModel


class ProjectFileSave(BaseModel):
    """Select exactly one owned upload or artifact and explicitly name its saved copy."""

    model_config = ConfigDict(extra="forbid")
    file_id: str | None = Field(default=None, min_length=1, max_length=36)
    artifact_id: str | None = Field(default=None, min_length=1, max_length=36)
    filename: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=128)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value):
        """Allow a display basename only, with no path components or control characters."""
        if (
            value != value.strip()
            or value in {"", ".", ".."}
            or any(
                character in "/\\:" or ord(character) < 32 or ord(character) == 127
                for character in value
            )
        ):
            raise ValueError("Use a filename, not a path")
        return value

    @model_validator(mode="after")
    def validate_source(self):
        """Reject ambiguous or missing source identities."""
        if (self.file_id is None) == (self.artifact_id is None):
            raise ValueError("Choose one file or artifact")
        return self


class ProjectFileRead(ApiModel):
    """Expose saved copy metadata without internal object keys or absolute paths."""

    project_file_id: str
    project_id: str
    filename: str
    source_type: str
    source_id: str
    size_bytes: int
    mime_type: str
    status: str
    created_at: datetime


class ProjectSessionCreate(BaseModel):
    """Create a project conversation with a retry-stable creation identity."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=128)
