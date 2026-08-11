"""Provider and selectable model discovery schemas."""

from nexuspilot_models.contracts import ProviderName
from pydantic import BaseModel


class ProviderCatalogResponse(BaseModel):
    """Describe registered providers and their configured model allowlists."""

    providers: list[ProviderName]
    models_by_provider: dict[ProviderName, list[str]]
