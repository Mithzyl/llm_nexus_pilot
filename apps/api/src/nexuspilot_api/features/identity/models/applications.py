"""Application ownership metadata; credentials and business bodies are never stored here."""

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class Application(TimestampMixin, Base):
    """Enable a trusted backend independently of its server-side credential configuration."""

    __tablename__ = "applications"
    application_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    is_enabled: Mapped[bool] = mapped_column(default=True)


class ApplicationUserMapping(TimestampMixin, Base):
    """Map an exact external subject to a private platform owner without creating a login."""

    __tablename__ = "application_user_mappings"
    __table_args__ = (
        UniqueConstraint("application_id", "external_user_id", name="uq_application_external_user"),
        UniqueConstraint("application_id", "pilot_principal_id", name="uq_application_principal"),
    )
    mapping_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.application_id"))
    external_user_id: Mapped[str] = mapped_column(
        String(128).with_variant(String(128, collation="utf8mb4_bin"), "mysql")
    )
    pilot_principal_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    is_enabled: Mapped[bool] = mapped_column(default=True)


class RunApplicationContext(Base):
    """Pin application identity and tool versions for a Run without copying business secrets."""

    __tablename__ = "run_application_context"
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.run_id"), primary_key=True)
    mapping_id: Mapped[str] = mapped_column(ForeignKey("application_user_mappings.mapping_id"))
    agent_id: Mapped[str] = mapped_column(String(64))
    allowed_tool_versions: Mapped[dict] = mapped_column(JSON)
