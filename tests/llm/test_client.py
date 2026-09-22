"""Tests for `app.llm.client`.

All chat calls go through a local fake `BaseChatModel` — no network I/O, no
real provider SDK calls, and tracing is exercised only via `Tracer.disabled()`
or a tracer built from settings where tracing is off. Nothing in this module
talks to Groq, OpenRouter, or LangSmith.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration
from langchain_core.outputs import ChatResult as LCChatResult

from app.config import Settings
from app.llm.base import ChatMessage, LLMError
from app.llm.client import LangChainLLMClient, Tracer, get_llm_client

MakeSettings = Callable[..., Settings]


class FakeChatModel(BaseChatModel):
    """A minimal fake `BaseChatModel` for tests: no network, fully controlled output."""

    response_content: str = "fake response"
    usage: dict[str, int] | None = None
    should_raise: bool = False
    seen_kwargs: list[dict[str, Any]] = []  # noqa: RUF012

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> LCChatResult:
        if self.should_raise:
            raise RuntimeError("boom-with-secret-key")
        self.seen_kwargs.append(kwargs)
        message = AIMessage(content=self.response_content, usage_metadata=self.usage)
        return LCChatResult(generations=[ChatGeneration(message=message)])


def _client(
    *, chat_model: BaseChatModel, tracer: Tracer | None = None, provider: str = "fake-provider"
) -> LangChainLLMClient:
    return LangChainLLMClient(
        chat_model=chat_model,
        provider=provider,
        model="fake-model",
        tracer=tracer or Tracer.disabled(),
    )


async def test_chat_maps_content_and_usage() -> None:
    model = FakeChatModel(
        response_content="hello there",
        usage={"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
    )
    client = _client(chat_model=model)

    result = await client.chat([ChatMessage(role="user", content="hi")])

    assert result.content == "hello there"
    assert result.provider == "fake-provider"
    assert result.model == "fake-model"
    assert result.usage is not None
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens == 8


async def test_chat_without_usage_metadata_returns_none_usage() -> None:
    model = FakeChatModel(response_content="no usage here", usage=None)
    client = _client(chat_model=model)

    result = await client.chat([ChatMessage(role="user", content="hi")])

    assert result.usage is None


async def test_chat_temperature_and_max_tokens_path() -> None:
    model = FakeChatModel()
    client = _client(chat_model=model)

    result = await client.chat(
        [ChatMessage(role="user", content="hi")],
        temperature=0.2,
        max_tokens=64,
    )

    assert result.content == "fake response"
    assert model.seen_kwargs, "bind() path should still invoke _generate"
    assert model.seen_kwargs[-1].get("temperature") == 0.2
    assert model.seen_kwargs[-1].get("max_tokens") == 64


async def test_chat_error_wraps_without_leaking_message() -> None:
    model = FakeChatModel(should_raise=True)
    client = _client(chat_model=model, provider="fake-provider")

    with pytest.raises(LLMError) as exc_info:
        await client.chat([ChatMessage(role="user", content="hi")])

    message = str(exc_info.value)
    assert "boom-with-secret-key" not in message
    assert "fake-provider" in message
    assert "RuntimeError" in message


async def test_chat_extracts_text_from_list_content_blocks() -> None:
    class _ListContentChatModel(FakeChatModel):
        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: CallbackManagerForLLMRun | None = None,
            **kwargs: Any,
        ) -> LCChatResult:
            message = AIMessage(content=[{"type": "text", "text": "hi"}])
            return LCChatResult(generations=[ChatGeneration(message=message)])

    client = _client(chat_model=_ListContentChatModel())

    result = await client.chat([ChatMessage(role="user", content="hi")])

    assert result.content == "hi"


async def test_chat_passes_run_name_in_config() -> None:
    model = FakeChatModel()
    client = _client(chat_model=model)

    seen_config: dict[str, Any] = {}
    original_ainvoke = BaseChatModel.ainvoke

    async def _spy_ainvoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        seen_config.update(kwargs.get("config") or {})
        return await original_ainvoke(self, *args, **kwargs)

    with patch.object(BaseChatModel, "ainvoke", _spy_ainvoke):
        await client.chat([ChatMessage(role="user", content="hi")])

    assert seen_config.get("run_name") == "llm.chat"
    assert seen_config.get("metadata") == {"provider": "fake-provider", "model": "fake-model"}


async def test_embed_raises_not_implemented() -> None:
    client = _client(chat_model=FakeChatModel())
    with pytest.raises(NotImplementedError):
        await client.embed(["text"])


async def test_vision_raises_not_implemented() -> None:
    client = _client(chat_model=FakeChatModel())
    with pytest.raises(NotImplementedError):
        await client.vision(b"bytes", "prompt")


def test_tracer_from_settings_disabled_when_tracing_false(make_settings: MakeSettings) -> None:
    settings = make_settings(langsmith_tracing=False, langsmith_api_key="a-key")
    tracer = Tracer.from_settings(settings)
    assert tracer._enabled is False  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_tracer_from_settings_disabled_when_key_missing(make_settings: MakeSettings) -> None:
    settings = make_settings(langsmith_tracing=True, langsmith_api_key=None)
    tracer = Tracer.from_settings(settings)
    assert tracer._enabled is False  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_get_llm_client_groq_construction_only(make_settings: MakeSettings) -> None:
    settings = make_settings(llm_provider="groq", groq_api_key="test-key")
    client = get_llm_client(settings)
    assert isinstance(client, LangChainLLMClient)
    assert client._model == settings.resolved_llm_model  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert client._provider == "groq"  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_get_llm_client_openrouter_construction_only(make_settings: MakeSettings) -> None:
    settings = make_settings(
        llm_provider="openrouter", openrouter_api_key="test-key", groq_api_key=None
    )
    client = get_llm_client(settings)
    assert isinstance(client, LangChainLLMClient)
    assert client._model == settings.resolved_llm_model  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert client._provider == "openrouter"  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
