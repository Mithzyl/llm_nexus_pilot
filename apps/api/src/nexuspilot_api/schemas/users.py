"""User request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nexuspilot_api.schemas.base import ApiModel


class UserCreate(BaseModel):
    """Validate a basic platform identity created by a trusted API caller."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=255)


class UserUpdate(BaseModel):
    """Allow trusted callers to update only mutable user profile fields."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None

    @model_validator(mode="after")
    def validate_non_empty_update(self) -> "UserUpdate":
        """Reject an update that does not contain any mutable field."""

        if self.display_name is None and self.is_active is None:
            raise ValueError("At least one user field must be provided")
        return self


class UserRead(ApiModel):
    """Expose the stable identity and activation state of a platform user."""

    user_id: str
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
