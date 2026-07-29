"""Shared Pydantic configuration for public HTTP schemas."""

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """Enable ORM-to-response conversion for all public API models."""

    model_config = ConfigDict(from_attributes=True)
