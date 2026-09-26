"""Concrete LLM client implementation and provider construction.

This is the ONLY module in the codebase allowed to import a model SDK /
LangChain chat-model integration. Everything else depends on the
`app.llm.base.LLMClient` protocol.
"""

import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Final, TypeVar, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langchain_openrouter import ChatOpenRouter
from langsmith import Client as LangSmithClient
from langsmith import trace as ls_trace
from langsmith.client import RUN_TYPE_T
from langsmith.run_helpers import tracing_context  # pyright: ignore[reportUnknownVariableType]
from pydantic import SecretStr

from app.config import LLMProvider, Settings
from app.llm.base import (
    ChatMessage,
    ChatResult,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    TokenUsage,
)

_T = TypeVar("_T")


def _to_langchain_message(message: ChatMessage) -> BaseMessage:
    """Convert our provider-agnostic `ChatMessage` to a langchain_core message."""
    if message.role == "system":
        return SystemMessage(content=message.content)
    if message.role == "user":
        return HumanMessage(content=message.content)
    return AIMessage(content=message.content)


#: Exactly the keys `app.graph.build._trace_inputs`/`_trace_outputs` emit for
#: the `teaching_graph` parent run -- the only payload shape allowed to reach
#: LangSmith unredacted. Kept honest by
#: `tests/llm/test_client.py::test_trace_inputs_and_outputs_keys_are_all_allow_listed`,
#: which calls those two functions directly and asserts every key they
#: produce is in this set; this module deliberately never imports
#: `app.graph.build` itself (that dependency would run the wrong way -- the
#: graph module already depends on this one for `Tracer`).
_ALLOWED_TRACE_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {
        # `_trace_inputs`
        "has_text",
        "has_image",
        "language",
        "user_id",
        "conversation_id",
        "max_llm_calls",
        # `_trace_outputs`
        "route",
        "intent",
        "topic",
        "verification_status",
        "error_count",
        "event_count",
        "llm_calls",
    }
)

#: The longest a `str` value is allowed to be for a payload to pass through
#: `redact_trace_payload` unchanged. Long enough for the enum-ish/id-ish
#: values `_trace_inputs`/`_trace_outputs` actually emit, short enough that no
#: meaningful chunk of learner text, code, or a traceback can hide inside one.
_MAX_TRACE_SCALAR_CHARS: Final = 64


def _is_safe_trace_scalar(value: object) -> bool:
    """Whether `value` is small and inert enough to leave the process."""
    if value is None or isinstance(value, bool | int | float):
        return True
    if isinstance(value, str):
        return len(value) <= _MAX_TRACE_SCALAR_CHARS
    return False


def redact_trace_payload(payload: object) -> dict[str, object]:
    """Redact one LangSmith run's `inputs`/`outputs` before they ever leave the process.

    This is wired onto `LangSmithClient(hide_inputs=..., hide_outputs=...)` in
    `Tracer.from_settings`, which makes it **client-wide**: every run this
    process's LangSmith client submits is passed through it -- not just the
    `teaching_graph` parent run `app.graph.build.run_graph` creates
    explicitly, but every node run LangGraph auto-instruments the moment the
    ambient tracing context is open (`understand_input`, `debug_agent`,
    `llm.chat`, ... every one of them), which otherwise carries the raw
    learner state -- text, code, images, tracebacks -- straight into
    LangSmith.

    A payload is passed through completely unchanged only when it is a
    non-empty `dict`, every key is one of `_ALLOWED_TRACE_METADATA_KEYS`
    (exactly what `app.graph.build._trace_inputs`/`_trace_outputs` emit for
    that one parent run), and every value is `None`, `bool`, `int`, `float`,
    or a `str` no longer than `_MAX_TRACE_SCALAR_CHARS`. That allow-list is
    the only reason the parent run's own metadata survives at all. Every
    other payload -- which is to say, every node's raw inputs/outputs -- is
    replaced by a marker (`{"redacted": True, "key_count": <n>}`) that names
    nothing about what was redacted: never a value, and never even an
    original key name outside the allow-list.
    """
    if isinstance(payload, dict):
        items = cast("dict[str, object]", payload)
        if items and all(
            key in _ALLOWED_TRACE_METADATA_KEYS and _is_safe_trace_scalar(value)
            for key, value in items.items()
        ):
            return items
        return {"redacted": True, "key_count": len(items)}
    return {"redacted": True, "key_count": 0}


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

        `hide_inputs`/`hide_outputs` are both `redact_trace_payload`, so the
        redaction is applied client-wide (see that function's docstring) --
        every run this client submits, including our own `teaching_graph`
        parent run, is passed through it before transmission.
        """
        if settings.langsmith_tracing and settings.langsmith_api_key is not None:
            client = LangSmithClient(
                api_key=settings.langsmith_api_key.get_secret_value(),
                hide_inputs=redact_trace_payload,
                hide_outputs=redact_trace_payload,
            )
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


_RATE_LIMIT_MARKERS: Final = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota",
    "insufficient_quota",
)
_MAX_CAUSE_DEPTH: Final = 5


def is_rate_limit(exc: BaseException) -> bool:
    """Whether `exc` (or something it wraps) is a provider rate/quota refusal.

    Provider SDKs surface a 429 in several shapes -- a typed `RateLimitError`,
    an HTTP error carrying `status_code`/`response.status_code`, or a plain
    message -- and LangChain wraps them further, so the cause chain is walked.
    The exception's text is only *inspected* here; none of it is ever put in
    the raised message, which stays provider + class name (see `LLMError`).
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(_MAX_CAUSE_DEPTH):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if "ratelimit" in type(current).__name__.replace("_", "").lower():
            return True
        status = getattr(current, "status_code", None)
        if status is None:
            status = getattr(getattr(current, "response", None), "status_code", None)
        if status == 429:
            return True
        text = str(current).lower()
        if "429" in text or any(marker in text for marker in _RATE_LIMIT_MARKERS):
            return True
        current = current.__cause__ or current.__context__
    return False


def _provider_error(provider: str, kind: str, exc: Exception) -> LLMError:
    """The `LLMError` (or `LLMRateLimitError`) to raise for a failed call."""
    message = f"{provider} {kind} call failed: {type(exc).__name__}"
    if is_rate_limit(exc):
        return LLMRateLimitError(message)
    return LLMError(message)


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
            raise _provider_error(self._provider, "chat", exc) from None

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
            raise _provider_error(self._provider, "vision", exc) from None

        return self._to_chat_result(
            ai_message, provider=self._provider, model=self._vision_model_name
        )


class FailoverLLMClient:
    """`LLMClient` that moves to the next credential when one is rate-limited.

    Holds one `LangChainLLMClient` per entry in `Settings.llm_failover_chain`
    (every configured Groq key in order, then OpenRouter). A call starts at
    whichever client last worked and advances ONLY on `LLMRateLimitError`;
    any other `LLMError` propagates untouched, because retrying a malformed
    request or a bad model id on another key just repeats the failure.

    The cursor is sticky for the life of the process and never rewinds. An
    exhausted key therefore costs one failed call in total, not one per turn.
    The cost of not rewinding is that a limit which later resets is not
    noticed -- deliberate, since re-probing a key that just returned 429 would
    spend a failed request on every call to find out.

    Not a `Protocol` implementation by inheritance: it satisfies `LLMClient`
    structurally, exactly as `LangChainLLMClient` does.
    """

    def __init__(self, clients: Sequence[LLMClient]) -> None:
        if not clients:
            raise ValueError("FailoverLLMClient requires at least one client")
        self._clients = list(clients)
        self._index = 0

    @property
    def clients(self) -> Sequence[LLMClient]:
        """The underlying clients, in failover order."""
        return tuple(self._clients)

    @property
    def active_index(self) -> int:
        """Index of the credential currently in use."""
        return self._index

    async def _attempt(self, call: Callable[[LLMClient], Awaitable[ChatResult]]) -> ChatResult:
        last: LLMRateLimitError | None = None
        index = self._index
        while index < len(self._clients):
            try:
                result = await call(self._clients[index])
            except LLMRateLimitError as exc:
                last = exc
                index += 1
                # Stay on the last client once exhausted rather than rewinding
                # to a key already known to be limited.
                self._index = min(index, len(self._clients) - 1)
                continue
            self._index = index
            return result
        if last is None:  # pragma: no cover - only reachable with an empty chain
            raise LLMError("no LLM credential is configured")
        raise last

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        return await self._attempt(
            lambda client: client.chat(messages, temperature=temperature, max_tokens=max_tokens)
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Delegate to the first client; embeddings come from fastembed, not a
        provider API, so there is nothing to fail over between."""
        return await self._clients[0].embed(texts)

    async def vision(
        self,
        image: bytes,
        prompt: str,
        *,
        mime_type: str = "image/png",
    ) -> ChatResult:
        return await self._attempt(lambda client: client.vision(image, prompt, mime_type=mime_type))


def _build_client(
    settings: Settings, provider: LLMProvider, api_key: SecretStr, tracer: Tracer
) -> LangChainLLMClient:
    """One credentialed `LangChainLLMClient` for `provider`."""
    model_name = settings.model_for(provider)
    vision_model_name = settings.vision_model_for(provider)

    chat_model: BaseChatModel
    vision_model: BaseChatModel
    if provider == "groq":
        chat_model = ChatGroq(model=model_name, api_key=api_key)
        vision_model = ChatGroq(model=vision_model_name, api_key=api_key)
    else:
        chat_model = ChatOpenRouter(model=model_name, api_key=api_key)
        vision_model = ChatOpenRouter(model=vision_model_name, api_key=api_key)

    return LangChainLLMClient(
        chat_model=chat_model,
        provider=provider,
        model=model_name,
        tracer=tracer,
        vision_model=vision_model,
        vision_model_name=vision_model_name,
    )


def get_llm_client(settings: Settings) -> LLMClient:
    """Build the configured `LLMClient` (Groq or OpenRouter) with tracing wired in.

    With more than one credential configured (several `GROQ_API_KEY_*`, or a
    Groq key plus an OpenRouter key) the result is a `FailoverLLMClient` over
    the whole of `Settings.llm_failover_chain`. With exactly one it is that
    single `LangChainLLMClient`: there is nothing to fail over to, so there is
    no reason to wrap it.
    """
    tracer = Tracer.from_settings(settings)
    chain = settings.llm_failover_chain
    if not chain:
        raise RuntimeError("no LLM provider API key is configured")

    clients = [_build_client(settings, provider, api_key, tracer) for provider, api_key in chain]
    if len(clients) == 1:
        return clients[0]
    return FailoverLLMClient(clients)
