"""Versioned instruction metadata; preference prose lives in encrypted object storage."""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class UserInstructionProfile(TimestampMixin, Base):
    """Own one global or entry-specific preference profile and its approved receiving entries."""

    __tablename__ = "user_instruction_profiles"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "scope_key", name="uq_instruction_owner_scope"),
    )
    profile_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    scope_key: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    allowed_entries_json: Mapped[list] = mapped_column(JSON, default=list)
    allowed_recipients_json: Mapped[dict] = mapped_column(JSON, default=dict)


class UserInstructionVersion(TimestampMixin, Base):
    """Keep immutable encrypted preference text without copying it into SQL."""

    __tablename__ = "user_instruction_versions"
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("user_instruction_profiles.profile_id"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    body_uri: Mapped[str] = mapped_column(String(1024))


class RunInstructionSelection(Base):
    """Pin both selected versions and explicit absence when the Run is created."""

    __tablename__ = "run_instruction_selections"
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), primary_key=True)
    entry: Mapped[str] = mapped_column(String(64))
    references_json: Mapped[list] = mapped_column(JSON)
