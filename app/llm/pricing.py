"""Per-model token prices, for the cost estimate on each turn's root trace run.

Prices are USD per million tokens, (input, output). They are list prices
copied by hand and will drift: the figure on a trace is an ESTIMATE for
spotting expensive turns, not billing. A model missing from the table
contributes tokens but no cost, so `cost_usd` is a lower bound whenever
`priced_calls < calls`. Free-tier OpenRouter models (`:free`) cost 0.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from app.llm.base import TokenUsage

__all__ = ["MODEL_PRICES_PER_MTOK", "estimate_cost_usd"]

MODEL_PRICES_PER_MTOK: Final[Mapping[str, tuple[float, float]]] = MappingProxyType(
    {
        # Groq list prices.
        "openai/gpt-oss-120b": (0.15, 0.60),
        "openai/gpt-oss-20b": (0.075, 0.30),
    }
)


def estimate_cost_usd(model: str, usage: TokenUsage) -> float | None:
    """Estimated USD cost of one call, or `None` when `model` is unpriced."""
    if model.endswith(":free"):
        return 0.0
    prices = MODEL_PRICES_PER_MTOK.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000
