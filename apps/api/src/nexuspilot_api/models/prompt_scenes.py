"""Approved per-entry prompt bindings and immutable Run selections."""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class PromptSceneBinding(TimestampMixin, Base):
    """Select a published template for one application, scene and execution mode."""

    __tablename__ = "prompt_scene_bindings"
    __table_args__ = (
        UniqueConstraint("entry", "scene_id", "execution_mode", name="uq_prompt_scene_mode"),
    )
    binding_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entry: Mapped[str] = mapped_column(String(64))
    scene_id: Mapped[str] = mapped_column(String(64))
    execution_mode: Mapped[str] = mapped_column(String(16))
    template_version_id: Mapped[str] = mapped_column(
        ForeignKey("llm_prompt_template_versions.template_version_id")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class RunPromptSceneSelection(Base):
    """Pin approved versions or absence at creation; never select a new scene during recovery."""

    __tablename__ = "run_prompt_scene_selections"
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), primary_key=True)
    entry: Mapped[str] = mapped_column(String(64))
    explicit_scene: Mapped[bool] = mapped_column(Boolean)
    selections_json: Mapped[list] = mapped_column(JSON)
