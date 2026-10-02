"""Per-turn token + cost accounting (ADAPTIVE-upgrade P0)."""

from collections.abc import Sequence

import pytest

from app.llm.base import ChatMessage, ChatResult, TokenUsage
from app.llm.budget import BudgetedLLMClient
from app.llm.pricing import estimate_cost_usd


class _UsageClient:
    def __init__(self, model: str, usage: TokenUsage | None) -> None:
        self._model = model
        self._usage = usage

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        return ChatResult(content="ok", provider="groq", model=self._model, usage=self._usage)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[0.0] for _ in texts]

    async def vision(
        self, image: bytes, prompt: str, *, mime_type: str = "image/png"
    ) -> ChatResult:
        return await self.chat([])


def test_estimate_cost_known_free_and_unknown_models() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000)
    assert estimate_cost_usd("openai/gpt-oss-120b", usage) == pytest.approx(0.75)
    assert estimate_cost_usd("some/model:free", usage) == 0.0
    assert estimate_cost_usd("unknown/model", usage) is None


async def test_budgeted_client_accumulates_usage_and_cost() -> None:
    usage = TokenUsage(input_tokens=1000, output_tokens=500, total_tokens=1500)
    client = BudgetedLLMClient(_UsageClient("openai/gpt-oss-120b", usage), max_calls=5)
    await client.chat([])
    await client.vision(b"x", "p")
    summary = client.usage_summary()
    assert summary["input_tokens"] == 2000
    assert summary["output_tokens"] == 1000
    assert summary["total_tokens"] == 3000
    assert summary["priced_calls"] == 2
    assert summary["cost_usd"] == pytest.approx(2 * (1000 * 0.15 + 500 * 0.60) / 1e6)


async def test_unpriced_or_usage_less_calls_add_no_cost() -> None:
    client = BudgetedLLMClient(_UsageClient("unknown/model", None), max_calls=5)
    await client.chat([])
    assert client.usage_summary() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "priced_calls": 0,
    }
