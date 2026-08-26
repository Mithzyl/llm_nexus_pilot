"""Evaluation rule sets and Lesson Candidate persistence models."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id, utc_now
from nexuspilot_api.models.enums import EvaluationRuleSetStatus, LessonCandidateStatus


class LlmEvaluationRuleSet(TimestampMixin, Base):
    """Represent one versioned, independently enableable set of evaluation rules."""

    __tablename__ = "llm_evaluation_rule_sets"
    __table_args__ = (
        UniqueConstraint("name", "schema_version", name="uq_evaluation_rule_set_version"),
        Index(
            "ix_evaluation_rule_set_status_updated",
            "status",
            "updated_at",
            "rule_set_id",
        ),
    )

    rule_set_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[EvaluationRuleSetStatus] = mapped_column(
        Enum(EvaluationRuleSetStatus, native_enum=False, length=32),
        default=EvaluationRuleSetStatus.ENABLED,
        index=True,
    )
    rule_count: Mapped[int] = mapped_column(Integer, default=0)


class LlmEvaluationRule(Base):
    """Represent one immutable deterministic rule inside a rule set version."""

    __tablename__ = "llm_evaluation_rules"
    __table_args__ = (
        UniqueConstraint(
            "rule_set_id",
            "rule_key",
            name="uq_evaluation_rule_key",
        ),
        Index("ix_evaluation_rule_set_created", "rule_set_id", "created_at"),
    )

    rule_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    rule_set_id: Mapped[str] = mapped_column(
        ForeignKey("llm_evaluation_rule_sets.rule_set_id", ondelete="CASCADE"),
        index=True,
    )
    rule_key: Mapped[str] = mapped_column(String(128))
    rule_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(32), default="error")
    config_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )


class LlmLessonCandidate(Base):
    """Represent one evidence-backed lesson awaiting independent review before promotion."""

    __tablename__ = "llm_lesson_candidates"
    __table_args__ = (
        Index(
            "ix_lesson_candidate_run_status_created",
            "run_id",
            "status",
            "created_at",
            "lesson_candidate_id",
        ),
        Index(
            "ix_lesson_candidate_task_status_created",
            "task_id",
            "status",
            "created_at",
            "lesson_candidate_id",
        ),
    )

    lesson_candidate_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_id
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("llm_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_tasks.task_id", ondelete="CASCADE"),
        index=True,
    )
    agent_handoff_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_agent_handoffs.agent_handoff_id", ondelete="SET NULL"),
        index=True,
    )
    evaluation_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_evaluations.evaluation_id", ondelete="SET NULL"),
        index=True,
    )
    status: Mapped[LessonCandidateStatus] = mapped_column(
        Enum(LessonCandidateStatus, native_enum=False, length=32),
        default=LessonCandidateStatus.CANDIDATE,
        index=True,
    )
    scope_json: Mapped[dict] = mapped_column(JSON)
    lesson_json: Mapped[dict] = mapped_column(JSON)
    valid_conditions_json: Mapped[dict] = mapped_column(JSON)
    invalid_conditions_json: Mapped[dict] = mapped_column(JSON)
    promoted_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("llm_memories.memory_id", ondelete="SET NULL"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=func.now(),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
