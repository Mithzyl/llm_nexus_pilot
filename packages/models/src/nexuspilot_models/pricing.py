"""Explicit versionable model-price configuration and decimal cost estimation."""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from nexuspilot_models.contracts import ProviderName


@dataclass(frozen=True)
class ModelPrice:
    """Define USD rates for ordinary input, cache reads, cache writes, and output."""

    input_per_million: Decimal
    output_per_million: Decimal
    cached_input_per_million: Decimal | None = None
    cache_write_input_per_million: Decimal | None = None


class PriceCatalog:
    """Estimate costs only for explicitly configured provider/model combinations."""

    def __init__(self, prices: dict[tuple[ProviderName, str], ModelPrice] | None = None) -> None:
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
        cache_write_tokens: int | None = None,
    ) -> Decimal | None:
        """Return a six-decimal USD estimate using disjoint normalized input classes."""

        price = self._prices.get((provider, model))
        if price is None or input_tokens is None or output_tokens is None:
            return None
        cached = min(cached_tokens or 0, input_tokens)
        cache_write = min(cache_write_tokens or 0, input_tokens - cached)
        uncached = input_tokens - cached - cache_write
        cached_rate = price.cached_input_per_million or price.input_per_million
        cache_write_rate = (
            price.cache_write_input_per_million or price.input_per_million
        )
        cost = (
            Decimal(uncached) * price.input_per_million
            + Decimal(cached) * cached_rate
            + Decimal(cache_write) * cache_write_rate
            + Decimal(output_tokens) * price.output_per_million
        ) / Decimal(1_000_000)
        return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    def estimate_maximum(
        self,
        provider: ProviderName,
        model: str,
        *,
        input_tokens_upper_bound: int,
        output_tokens_upper_bound: int,
    ) -> Decimal | None:
        """Estimate a conservative call cost from explicit input and output token bounds."""

        price = self._prices.get((provider, model))
        if price is None:
            return None
        input_rate = max(
            price.input_per_million,
            price.cached_input_per_million or price.input_per_million,
            price.cache_write_input_per_million or price.input_per_million,
        )
        cost = (
            Decimal(input_tokens_upper_bound) * input_rate
            + Decimal(output_tokens_upper_bound) * price.output_per_million
        ) / Decimal(1_000_000)
        return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
