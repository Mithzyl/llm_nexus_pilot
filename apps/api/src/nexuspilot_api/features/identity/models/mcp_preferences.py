"""User restrictions on administrator-approved MCP connections; no endpoints or credentials."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin


class MCPUserConnection(TimestampMixin, Base):
    """Pin a user's selected definitions and revision so configuration changes require review."""

    __tablename__ = "mcp_user_connections"
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    connection_alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    config_version: Mapped[str] = mapped_column(String(32))
    revision: Mapped[int] = mapped_column(default=1)
    enabled: Mapped[bool] = mapped_column(default=True)
    approved_tools_json: Mapped[dict] = mapped_column(JSON)
    discovery_status: Mapped[str | None] = mapped_column(String(64))
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
