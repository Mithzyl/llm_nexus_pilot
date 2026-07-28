"""Explicit versionable model-price configuration and decimal cost estimation."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from nexuspilot_models.contracts import ProviderName


@dataclass(frozen=True)
class ModelPrice:
    """Define USD prices per one million input, output, and cached input tokens."""

    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None


class PriceCatalog:
    """Estimate costs only for explicitly configured provider/model combinations."""

    def __init__(
        self, prices: dict[tuple[ProviderName, str], ModelPrice] | None = None
    ) -> None:
        """Create a catalog whose unknown models intentionally have no estimated cost."""

        self._prices = prices or {}

    def estimate(
        self,
        provider: ProviderName,
        model: str,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_tokens: int | None,
    ) -> Decimal | None:
        """Return a six-decimal USD estimate or None when price or usage is unknown."""

        price = self._prices.get((provider, model))
        if price is None or input_tokens is None or output_tokens is None:
            return None
        cached = min(cached_tokens or 0, input_tokens)
        uncached = input_tokens - cached
        cached_rate = price.cached_input_per_million or price.input_per_million
        cost = (
            Decimal(uncached) * price.input_per_million
            + Decimal(cached) * cached_rate
            + Decimal(output_tokens) * price.output_per_million
        ) / Decimal(1_000_000)
        return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
