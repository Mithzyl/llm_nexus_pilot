"""Project-owned immutable copies; source deletion does not delete explicitly saved copies."""

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class ProjectFile(TimestampMixin, Base):
    """Track publication and provenance without storing file bodies or public host paths."""

    __tablename__ = "project_workspace_files"
    __table_args__ = (
        UniqueConstraint("project_id", "filename", name="uq_project_file_name"),
        UniqueConstraint("project_id", "key_hash", name="uq_project_file_request"),
    )
    project_file_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("llm_projects.project_id", ondelete="RESTRICT"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(36))
    key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    storage_uri: Mapped[str] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mime_type: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="reserved")
