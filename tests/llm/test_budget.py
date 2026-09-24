"""Tests for `BudgetedLLMClient` / `LLMBudgetExceededError`."""

from collections.abc import Sequence

import pytest

from app.llm.base import ChatMessage, ChatResult, LLMClient
from app.llm.budget import BudgetedLLMClient, LLMBudgetExceededError


class _CountingLLMClient:
    """A minimal `LLMClient` implementing all three methods, for budget tests."""

    def __init__(self) -> None:
        self.chat_calls = 0
        self.embed_calls = 0
        self.vision_calls = 0

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        self.chat_calls += 1
        return ChatResult(content="ok", provider="fake", model="fake-model")

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.embed_calls += 1
        return [[0.0] for _ in texts]

    async def vision(
        self,
        image: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> ChatResult:
        self.vision_calls += 1
        return ChatResult(content="ok", provider="fake", model="fake-vision")


async def test_calls_counted_across_all_three_methods() -> None:
    inner = _CountingLLMClient()
    client = BudgetedLLMClient(inner, max_calls=5)

    await client.chat([ChatMessage(role="user", content="hi")])
    await client.embed(["hi"])
    await client.vision(b"data", "prompt")

    assert client.calls == 3
    assert inner.chat_calls == 1
    assert inner.embed_calls == 1
    assert inner.vision_calls == 1


async def test_call_over_budget_blocked_without_calling_inner() -> None:
    inner = _CountingLLMClient()
    client = BudgetedLLMClient(inner, max_calls=2)

    await client.chat([ChatMessage(role="user", content="hi")])
    await client.chat([ChatMessage(role="user", content="hi")])

    with pytest.raises(LLMBudgetExceededError):
        await client.chat([ChatMessage(role="user", content="hi")])

    assert client.calls == 2
    assert inner.chat_calls == 2


async def test_blocked_call_does_not_increment_further() -> None:
    inner = _CountingLLMClient()
    client = BudgetedLLMClient(inner, max_calls=1)

    await client.vision(b"data", "prompt")
    with pytest.raises(LLMBudgetExceededError):
        await client.vision(b"data", "prompt")
    with pytest.raises(LLMBudgetExceededError):
        await client.embed(["x"])

    assert client.calls == 1
    assert inner.vision_calls == 1
    assert inner.embed_calls == 0


def test_isinstance_llm_client() -> None:
    client = BudgetedLLMClient(_CountingLLMClient())
    assert isinstance(client, LLMClient)


async def test_max_calls_zero_blocks_everything() -> None:
    inner = _CountingLLMClient()
    client = BudgetedLLMClient(inner, max_calls=0)

    with pytest.raises(LLMBudgetExceededError):
        await client.chat([ChatMessage(role="user", content="hi")])

    assert client.calls == 0
    assert inner.chat_calls == 0


def test_negative_max_calls_rejected() -> None:
    with pytest.raises(ValueError, match="max_calls"):
        BudgetedLLMClient(_CountingLLMClient(), max_calls=-1)


def test_default_max_calls_used_when_unspecified() -> None:
    client = BudgetedLLMClient(_CountingLLMClient())
    assert client.max_calls == 3


def test_calls_property_starts_at_zero() -> None:
    client = BudgetedLLMClient(_CountingLLMClient(), max_calls=5)
    assert client.calls == 0
    assert client.max_calls == 5
