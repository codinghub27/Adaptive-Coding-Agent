"""Concrete LLM client implementation and provider construction.

This is the ONLY module in the codebase allowed to import a model SDK /
LangChain chat-model integration. Everything else depends on the
`app.llm.base.LLMClient` protocol.
"""

import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import TypeVar, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langchain_openrouter import ChatOpenRouter
from langsmith import Client as LangSmithClient
from langsmith import trace as ls_trace
from langsmith.client import RUN_TYPE_T
from langsmith.run_helpers import tracing_context  # pyright: ignore[reportUnknownVariableType]

from app.config import Settings
from app.llm.base import ChatMessage, ChatResult, LLMClient, LLMError, TokenUsage

_T = TypeVar("_T")


def _to_langchain_message(message: ChatMessage) -> BaseMessage:
    """Convert our provider-agnostic `ChatMessage` to a langchain_core message."""
    if message.role == "system":
        return SystemMessage(content=message.content)
    if message.role == "user":
        return HumanMessage(content=message.content)
    return AIMessage(content=message.content)


class Tracer:
    """Thin LangSmith tracing hook.

    When disabled (the default), tracing is a complete no-op: no LangSmith
    client is constructed and no environment variables are read or written.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        client: LangSmithClient | None = None,
        project_name: str | None = None,
    ) -> None:
        self._enabled = enabled
        self._client = client
        self._project_name = project_name

    @classmethod
    def from_settings(cls, settings: Settings) -> "Tracer":
        """Build a tracer from application settings.

        Tracing is only enabled when both `langsmith_tracing` is true and a
        LangSmith API key is configured. The client/project are passed
        explicitly rather than relying on ambient environment variables,
        since `Settings` never exports values to `os.environ`.
        """
        if settings.langsmith_tracing and settings.langsmith_api_key is not None:
            client = LangSmithClient(api_key=settings.langsmith_api_key.get_secret_value())
            return cls(enabled=True, client=client, project_name=settings.langsmith_project)
        return cls(enabled=False)

    @classmethod
    def disabled(cls) -> "Tracer":
        """A tracer that never sends anything to LangSmith. Useful for tests."""
        return cls(enabled=False)

    async def trace(self, fn: Callable[[], Awaitable[_T]]) -> _T:
        """Run `fn`, enabling LangSmith tracing context when configured.

        This does not create its own traced run; it only opens the ambient
        LangSmith tracing context so that LangChain's own run (e.g. the
        single LLM call inside `fn`) is captured, with its inputs, outputs,
        and token usage intact. When disabled, this is a complete no-op.
        """
        context: AbstractContextManager[None]
        if self._enabled:
            context = tracing_context(
                enabled=True, client=self._client, project_name=self._project_name
            )
        else:
            context = nullcontext()

        with context:
            return await fn()

    async def run(
        self,
        name: str,
        run_type: str,
        inputs: Mapping[str, object],
        fn: Callable[[], Awaitable[_T]],
        *,
        outputs: Callable[[_T], Mapping[str, object]] | None = None,
    ) -> _T:
        """Run `fn` inside a real, standalone LangSmith run.

        Unlike `trace` (which only opens ambient tracing context around an
        existing LangChain-produced run), this creates the run itself, for
        call sites that emit no LangChain run of their own (e.g. local
        fastembed inference). A complete no-op (`await fn()`, no LangSmith
        client interaction) when tracing is disabled.

        `inputs` must only ever be small, non-sensitive metadata (counts,
        model names) -- never raw user-supplied text, which may be large or
        untrusted. `outputs`, if given, computes the run's recorded output
        from `fn`'s result.
        """
        if not self._enabled:
            return await fn()

        with tracing_context(enabled=True, client=self._client, project_name=self._project_name):
            async with ls_trace(
                name=name,
                run_type=cast("RUN_TYPE_T", run_type),
                inputs=dict(inputs),
                client=self._client,
                project_name=self._project_name,
            ) as run_tree:
                result = await fn()
                if outputs is not None:
                    run_tree.end(outputs=dict(outputs(result)))  # pyright: ignore[reportUnknownMemberType]
                return result


class LangChainLLMClient:
    """`LLMClient` implementation backed by a LangChain `BaseChatModel`."""

    def __init__(
        self,
        *,
        chat_model: BaseChatModel,
        provider: str,
        model: str,
        tracer: Tracer,
        vision_model: BaseChatModel | None = None,
        vision_model_name: str | None = None,
    ) -> None:
        self._chat_model = chat_model
        self._provider = provider
        self._model = model
        self._tracer = tracer
        self._vision_model = vision_model
        self._vision_model_name = vision_model_name

    @staticmethod
    def _to_chat_result(ai_message: AIMessage, *, provider: str, model: str) -> ChatResult:
        """Map a LangChain `AIMessage` into our provider-agnostic `ChatResult`."""
        content = ai_message.text

        usage: TokenUsage | None = None
        usage_metadata = ai_message.usage_metadata
        if usage_metadata is not None:
            usage = TokenUsage(
                input_tokens=usage_metadata["input_tokens"],
                output_tokens=usage_metadata["output_tokens"],
                total_tokens=usage_metadata["total_tokens"],
            )

        return ChatResult(
            content=content,
            provider=provider,
            model=model,
            usage=usage,
        )

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        lc_messages = [_to_langchain_message(message) for message in messages]
        bind_kwargs: dict[str, object] = {}
        if temperature is not None:
            bind_kwargs["temperature"] = temperature
        if max_tokens is not None:
            bind_kwargs["max_tokens"] = max_tokens

        run_config: RunnableConfig = {
            "run_name": "llm.chat",
            "metadata": {"provider": self._provider, "model": self._model},
        }

        async def _invoke() -> AIMessage:
            if bind_kwargs:
                bound = self._chat_model.bind(**bind_kwargs)
                return await bound.ainvoke(lc_messages, config=run_config)
            return await self._chat_model.ainvoke(lc_messages, config=run_config)

        try:
            ai_message = await self._tracer.trace(_invoke)
        except Exception as exc:
            raise LLMError(f"{self._provider} chat call failed: {type(exc).__name__}") from None

        return self._to_chat_result(ai_message, provider=self._provider, model=self._model)

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError("embeddings not implemented until Phase 5")

    async def vision(
        self,
        image: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> ChatResult:
        if self._vision_model is None or self._vision_model_name is None:
            raise LLMError(f"{self._provider} vision model not configured")

        b64_image = base64.b64encode(image).decode("ascii")
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64_image}"},
                },
            ]
        )

        run_config: RunnableConfig = {
            "run_name": "llm.vision",
            "metadata": {"provider": self._provider, "model": self._vision_model_name},
        }

        vision_model = self._vision_model

        async def _invoke() -> AIMessage:
            return await vision_model.ainvoke([message], config=run_config)

        try:
            ai_message = await self._tracer.trace(_invoke)
        except Exception as exc:
            raise LLMError(f"{self._provider} vision call failed: {type(exc).__name__}") from None

        return self._to_chat_result(
            ai_message, provider=self._provider, model=self._vision_model_name
        )


def get_llm_client(settings: Settings) -> LLMClient:
    """Build the configured `LLMClient` (Groq or OpenRouter) with tracing wired in."""
    tracer = Tracer.from_settings(settings)
    model_name = settings.resolved_llm_model
    vision_model_name = settings.resolved_llm_vision_model
    api_key = settings.llm_api_key

    chat_model: BaseChatModel
    vision_model: BaseChatModel
    if settings.llm_provider == "groq":
        chat_model = ChatGroq(model=model_name, api_key=api_key)
        vision_model = ChatGroq(model=vision_model_name, api_key=api_key)
    else:
        chat_model = ChatOpenRouter(model=model_name, api_key=api_key)
        vision_model = ChatOpenRouter(model=vision_model_name, api_key=api_key)

    return LangChainLLMClient(
        chat_model=chat_model,
        provider=settings.llm_provider,
        model=model_name,
        tracer=tracer,
        vision_model=vision_model,
        vision_model_name=vision_model_name,
    )
