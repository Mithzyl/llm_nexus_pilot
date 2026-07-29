"""User request and response schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from nexuspilot_api.schemas.base import ApiModel


class UserCreate(BaseModel):
    """Validate a basic platform identity created by a trusted API caller."""

    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=255)


class UserRead(ApiModel):
    """Expose the stable identity and activation state of a platform user."""

    user_id: str
    display_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
