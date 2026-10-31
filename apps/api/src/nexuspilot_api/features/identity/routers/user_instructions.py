"""Manage explicit user instructions without publishing internal system prompts."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from nexuspilot_api.features.identity.services.user_instructions import get_profile, save_profile
from nexuspilot_api.features.memory.routers.product_memory import memory_http_error
from nexuspilot_api.features.memory.routers.sharing import authenticated_service
from nexuspilot_api.features.memory.services.product_memory import MemoryFailure
from nexuspilot_api.routers.common import DatabaseSessionDependency, ObjectStorageDependency

router = APIRouter(tags=["user-instructions"])


class InstructionCommand(BaseModel):
    """Versioned preferences and receiving entries, without model or permission overrides."""

    model_config = ConfigDict(extra="forbid")
    scope: Literal["global", "entry"] = "entry"
    text: str = Field(min_length=1, max_length=4000)
    enabled: bool
    expected_version: int = Field(ge=0)
    allowed_entries: list[Literal["native", "personal_assistant", "retail"]] = Field(
        default_factory=list, max_length=3
    )


@router.get("/me/instructions")
async def read_instructions(
    db: DatabaseSessionDependency,
    storage: ObjectStorageDependency,
    scope: Literal["global", "entry"] = "entry",
):
    """Read only this authenticated user's current instruction settings."""
    try:
        identity = await authenticated_service(db, storage)
        return await get_profile(db, storage, identity.owner, identity.entry, scope)
    except MemoryFailure as error:
        raise memory_http_error(error) from None


@router.post("/me/instructions")
async def write_instructions(
    payload: InstructionCommand, db: DatabaseSessionDependency, storage: ObjectStorageDependency
):
    """Save a new version for future Runs; existing Runs continue using their pinned versions."""
    try:
        identity = await authenticated_service(db, storage)
        return await save_profile(
            db, storage, identity.owner, identity.entry, **payload.model_dump()
        )
    except MemoryFailure as error:
        raise memory_http_error(error) from None
