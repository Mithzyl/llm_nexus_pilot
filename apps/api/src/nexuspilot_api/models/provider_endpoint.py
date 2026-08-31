"""Database-managed Provider endpoint addresses without Provider credentials."""

from nexuspilot_models.contracts import ProviderName
from sqlalchemy import Boolean, Computed, Enum, Index, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from nexuspilot_api.models.base import Base, TimestampMixin, new_id


class LlmProviderEndpoint(TimestampMixin, Base):
    """Store one Provider Base URL and whether it participates in runtime registration."""

    __tablename__ = "llm_provider_endpoints"
    __table_args__ = (
        UniqueConstraint(
            "enabled_provider",
            name="uq_llm_provider_endpoint_enabled_provider",
        ),
        Index(
            "ix_llm_provider_endpoint_provider_enabled",
            "provider",
            "enabled",
        ),
    )

    provider_endpoint_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=new_id,
    )
    provider: Mapped[ProviderName] = mapped_column(
        Enum(
            ProviderName,
            values_callable=lambda provider_names: [
                provider_name.value for provider_name in provider_names
            ],
            native_enum=False,
            length=32,
        )
    )
    base_url: Mapped[str] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
    )
    # NULL values do not collide in a unique constraint, so disabled history may coexist.
    enabled_provider: Mapped[str | None] = mapped_column(
        String(32),
        Computed(
            "CASE WHEN enabled = 1 THEN provider ELSE NULL END",
            persisted=True,
        ),
        nullable=True,
    )
