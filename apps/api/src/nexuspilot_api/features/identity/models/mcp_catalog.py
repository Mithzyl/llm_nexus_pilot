"""Immutable per-user MCP catalog aliases tied to a specific approval revision."""

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base


class MCPToolBinding(Base):
    """Resolve a model-visible random alias without accepting model-supplied connection identity."""

    __tablename__ = "mcp_tool_bindings"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "connection_alias",
            "config_version",
            "permission_revision",
            "remote_tool_name",
            name="uq_mcp_tool_approval",
        ),
    )
    tool_alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    connection_alias: Mapped[str] = mapped_column(String(64))
    config_version: Mapped[str] = mapped_column(String(32))
    permission_revision: Mapped[int] = mapped_column()
    remote_tool_name: Mapped[str] = mapped_column(String(128))
    definition_json: Mapped[dict] = mapped_column(JSON)
