"""Platform identity persistence models."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    """Represent a platform user referenced by authenticated run records."""

    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
