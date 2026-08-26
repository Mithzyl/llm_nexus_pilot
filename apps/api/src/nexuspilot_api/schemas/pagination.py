"""Reusable public cursor-page response schemas."""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

ItemType = TypeVar("ItemType")


class CursorPage(BaseModel, Generic[ItemType]):
    """Expose a bounded resource page and an opaque cursor for the next page."""

    items: list[ItemType]
    next_cursor: str | None = None
    has_more: bool
    limit: int = Field(ge=1)
