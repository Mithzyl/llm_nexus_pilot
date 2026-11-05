"""Persist caller processing identity and outcome references without storing body text in SQL."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class ApplicationResponseExecution(TimestampMixin, Base):
    """One stable execution per saved model response; ambiguous dispatch is never replayed."""

    __tablename__ = "application_response_executions"
    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), index=True)
    response_id: Mapped[str] = mapped_column(ForeignKey("llm_attempts.attempt_id"), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    request_uri: Mapped[str] = mapped_column(String(1024))
    result_uri: Mapped[str | None] = mapped_column(String(1024))
    destination: Mapped[str] = mapped_column(String(2048))


class ApplicationResponseBinding(TimestampMixin, Base):
    """Pin non-secret caller connection fields at workflow creation, not at first dispatch."""

    __tablename__ = "application_response_bindings"
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), primary_key=True)
    mapping_id: Mapped[str] = mapped_column(ForeignKey("application_user_mappings.mapping_id"))
    connection_json: Mapped[dict] = mapped_column(JSON)
