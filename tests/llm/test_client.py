"""Tests for `app.llm.client`.

All chat calls go through a local fake `BaseChatModel` — no network I/O, no
real provider SDK calls, and tracing is exercised only via `Tracer.disabled()`
or a tracer built from settings where tracing is off. Nothing in this module
talks to Groq, OpenRouter, or a real LangSmith backend; the `Tracer.run` tests
below use a `unittest.mock.MagicMock(spec=Client)` in place of a real
`langsmith.Client`, so no network I/O happens there either.
"""

from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration
from langchain_core.outputs import ChatResult as LCChatResult
from langsmith import Client as LangSmithClient

from app.config import OPENROUTER_DEFAULT_MODEL, Settings
from app.graph.build import (
    _trace_inputs,  # pyright: ignore[reportPrivateUsage]
    _trace_outputs,  # pyright: ignore[reportPrivateUsage]
)
from app.graph.state import AgentState, RawInput
from app.llm.base import ChatMessage, ChatResult, LLMError, LLMRateLimitError
from app.llm.budget import BudgetedLLMClient
from app.llm.client import (
    _ALLOWED_TRACE_METADATA_KEYS,  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    FailoverLLMClient,
    LangChainLLMClient,
    Tracer,
    get_llm_client,
    is_rate_limit,
    redact_trace_payload,
)
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]


class FakeChatModel(BaseChatModel):
    """A minimal fake `BaseChatModel` for tests: no network, fully controlled output."""

    response_content: str = "fake response"
    usage: dict[str, int] | None = None
    should_raise: bool = False
    seen_kwargs: list[dict[str, Any]] = []  # noqa: RUF012
    seen_messages: list[list[BaseMessage]] = []  # noqa: RUF012

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
        self.seen_messages.append(messages)
        message = AIMessage(content=self.response_content, usage_metadata=self.usage)
        return LCChatResult(generations=[ChatGeneration(message=message)])


def _client(
    *,
    chat_model: BaseChatModel,
    tracer: Tracer | None = None,
    provider: str = "fake-provider",
    vision_model: BaseChatModel | None = None,
    vision_model_name: str | None = None,
) -> LangChainLLMClient:
    return LangChainLLMClient(
        chat_model=chat_model,
        provider=provider,
        model="fake-model",
        tracer=tracer or Tracer.disabled(),
        vision_model=vision_model,
        vision_model_name=vision_model_name,
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


async def test_vision_maps_content_and_usage_and_sends_image_message() -> None:
    chat_model = FakeChatModel()
    vision_model = FakeChatModel(
        response_content="transcribed text",
        usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )
    client = _client(
        chat_model=chat_model,
        vision_model=vision_model,
        vision_model_name="fake-vision-model",
    )

    result = await client.vision(b"\x89PNG\r\n\x1a\nrest", "describe this")

    assert result.content == "transcribed text"
    assert result.provider == "fake-provider"
    assert result.model == "fake-vision-model"
    assert result.usage is not None
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 20
    assert result.usage.total_tokens == 30

    assert vision_model.seen_messages, "vision model should have been invoked"
    sent_messages = vision_model.seen_messages[-1]
    assert len(sent_messages) == 1
    content = sent_messages[0].content
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "describe this"}
    image_block = content[1]
    assert isinstance(image_block, dict)
    assert image_block["type"] == "image_url"
    assert image_block["image_url"]["url"].startswith("data:image/png;base64,")


async def test_vision_passes_run_name_and_metadata() -> None:
    vision_model = FakeChatModel()
    client = _client(
        chat_model=FakeChatModel(),
        vision_model=vision_model,
        vision_model_name="fake-vision-model",
    )

    seen_config: dict[str, Any] = {}
    original_ainvoke = BaseChatModel.ainvoke

    async def _spy_ainvoke(self: Any, *args: Any, **kwargs: Any) -> Any:
        seen_config.update(kwargs.get("config") or {})
        return await original_ainvoke(self, *args, **kwargs)

    with patch.object(BaseChatModel, "ainvoke", _spy_ainvoke):
        await client.vision(b"bytes", "prompt")

    assert seen_config.get("run_name") == "llm.vision"
    assert seen_config.get("metadata") == {
        "provider": "fake-provider",
        "model": "fake-vision-model",
    }


async def test_vision_error_wraps_without_leaking_message() -> None:
    vision_model = FakeChatModel(should_raise=True)
    client = _client(
        chat_model=FakeChatModel(),
        vision_model=vision_model,
        vision_model_name="fake-vision-model",
        provider="fake-provider",
    )

    with pytest.raises(LLMError) as exc_info:
        await client.vision(b"bytes", "prompt")

    message = str(exc_info.value)
    assert "boom-with-secret-key" not in message
    assert "fake-provider" in message
    assert "RuntimeError" in message


async def test_vision_without_vision_model_raises_llm_error() -> None:
    client = _client(chat_model=FakeChatModel())

    with pytest.raises(LLMError) as exc_info:
        await client.vision(b"bytes", "prompt")

    assert "not configured" in str(exc_info.value)


async def test_get_llm_client_builds_vision_model(make_settings: MakeSettings) -> None:
    # `openrouter_api_key=None` keeps this a SINGLE-credential build. The
    # `make_settings` fixture keys both providers, which is now a two-link
    # failover chain and therefore a `FailoverLLMClient`; this test is about
    # how one credentialed client is constructed, so it pins one credential.
    settings = make_settings(llm_provider="groq", groq_api_key="test-key", openrouter_api_key=None)
    client = get_llm_client(settings)
    assert isinstance(client, LangChainLLMClient)
    assert client._vision_model is not None  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert (
        client._vision_model_name  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        == settings.resolved_llm_vision_model
    )


def test_tracer_from_settings_disabled_when_tracing_false(make_settings: MakeSettings) -> None:
    settings = make_settings(langsmith_tracing=False, langsmith_api_key="a-key")
    tracer = Tracer.from_settings(settings)
    assert tracer._enabled is False  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_tracer_from_settings_disabled_when_key_missing(make_settings: MakeSettings) -> None:
    settings = make_settings(langsmith_tracing=True, langsmith_api_key=None)
    tracer = Tracer.from_settings(settings)
    assert tracer._enabled is False  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_get_llm_client_groq_construction_only(make_settings: MakeSettings) -> None:
    # Single credential on purpose -- see `test_get_llm_client_builds_vision_model`.
    settings = make_settings(llm_provider="groq", groq_api_key="test-key", openrouter_api_key=None)
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


# --------------------------------------------------------------------------
# Tracer.run
# --------------------------------------------------------------------------


def _fake_langsmith_client() -> LangSmithClient:
    """A `MagicMock(spec=Client)` standing in for a real `langsmith.Client`:
    `create_run`/`update_run` calls are recorded, never sent over the network."""
    return cast(LangSmithClient, MagicMock(spec=LangSmithClient))


async def test_tracer_run_disabled_is_noop_passthrough() -> None:
    tracer = Tracer.disabled()
    calls: list[str] = []

    async def _fn() -> str:
        calls.append("called")
        return "result"

    result = await tracer.run("op", "tool", {"should": "be-ignored"}, _fn)

    assert result == "result"
    assert calls == ["called"]


async def test_tracer_run_enabled_creates_run_with_expected_name_and_inputs() -> None:
    mock_client = _fake_langsmith_client()
    tracer = Tracer(enabled=True, client=mock_client, project_name="test-project")

    async def _fn() -> dict[str, int]:
        return {"n": 2}

    result = await tracer.run(
        "embedding.embed_passages",
        "embedding",
        {"model": "fake-model", "n_texts": 2},
        _fn,
        outputs=lambda r: {"n_vectors": r["n"]},
    )

    assert result == {"n": 2}
    create_run = cast(MagicMock, mock_client.create_run)  # pyright: ignore[reportAttributeAccessIssue]
    assert create_run.called
    kwargs = create_run.call_args.kwargs
    assert kwargs["name"] == "embedding.embed_passages"
    assert kwargs["run_type"] == "embedding"
    assert kwargs["inputs"] == {"model": "fake-model", "n_texts": 2}

    update_run = cast(MagicMock, mock_client.update_run)  # pyright: ignore[reportUnknownMemberType]
    assert update_run.called
    patch_kwargs = update_run.call_args.kwargs
    assert patch_kwargs.get("outputs") == {"n_vectors": 2}


async def test_tracer_run_enabled_without_outputs_fn_does_not_call_end() -> None:
    mock_client = _fake_langsmith_client()
    tracer = Tracer(enabled=True, client=mock_client, project_name="test-project")

    async def _fn() -> str:
        return "value"

    result = await tracer.run("op", "tool", {"n": 1}, _fn)

    assert result == "value"
    create_run = cast(MagicMock, mock_client.create_run)  # pyright: ignore[reportAttributeAccessIssue]
    assert create_run.called


# --------------------------------------------------------------------------
# Rate-limit classification and credential failover
# --------------------------------------------------------------------------


class _FakeRateLimitError(Exception):
    """Stands in for a provider SDK's typed 429, which is never imported here."""

    status_code = 429


class _StubClient:
    """A minimal `LLMClient` that records calls and fails on demand."""

    def __init__(self, name: str, *, fail: Exception | None = None) -> None:
        self.name = name
        self.fail = fail
        self.calls = 0

    async def chat(
        self,
        messages: object,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        del messages, temperature, max_tokens
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return ChatResult(content=self.name, provider="stub", model=self.name)

    async def embed(self, texts: object) -> list[list[float]]:
        del texts
        self.calls += 1
        return [[0.0]]

    async def vision(
        self, image: bytes, prompt: str, *, mime_type: str = "image/png"
    ) -> ChatResult:
        del image, prompt, mime_type
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return ChatResult(content=self.name, provider="stub", model=self.name)


@pytest.mark.parametrize(
    "exc",
    [
        _FakeRateLimitError(),
        Exception("Error code: 429 - rate limit reached for model"),
        Exception("You exceeded your current quota"),
        Exception("Too Many Requests"),
    ],
)
def testis_rate_limit_recognises_provider_shapes(exc: Exception) -> None:
    assert is_rate_limit(exc) is True


@pytest.mark.parametrize(
    "exc",
    [Exception("connection reset"), Exception("404 model not found"), ValueError("bad request")],
)
def testis_rate_limit_rejects_other_failures(exc: Exception) -> None:
    assert is_rate_limit(exc) is False


def testis_rate_limit_walks_the_cause_chain() -> None:
    inner = _FakeRateLimitError()
    outer = Exception("langchain wrapped this")
    outer.__cause__ = inner
    assert is_rate_limit(outer) is True


async def test_failover_advances_past_a_rate_limited_key() -> None:
    first = _StubClient("k1", fail=LLMRateLimitError("groq chat call failed: RateLimitError"))
    second = _StubClient("k2")
    client = FailoverLLMClient([first, second])

    result = await client.chat([ChatMessage(role="user", content="hi")])

    assert result.content == "k2"
    assert (first.calls, second.calls) == (1, 1)


async def test_failover_cursor_is_sticky() -> None:
    first = _StubClient("k1", fail=LLMRateLimitError("limited"))
    second = _StubClient("k2")
    client = FailoverLLMClient([first, second])

    await client.chat([ChatMessage(role="user", content="a")])
    await client.chat([ChatMessage(role="user", content="b")])

    # The exhausted key costs ONE failed call in total, not one per turn.
    assert first.calls == 1
    assert second.calls == 2
    assert client.active_index == 1


async def test_failover_does_not_burn_keys_on_a_non_rate_limit_error() -> None:
    first = _StubClient("k1", fail=LLMError("groq chat call failed: BadRequestError"))
    second = _StubClient("k2")
    client = FailoverLLMClient([first, second])

    with pytest.raises(LLMError):
        await client.chat([ChatMessage(role="user", content="hi")])

    assert second.calls == 0
    assert client.active_index == 0


async def test_failover_raises_when_every_credential_is_limited() -> None:
    clients = [_StubClient(f"k{i}", fail=LLMRateLimitError("limited")) for i in range(3)]
    client = FailoverLLMClient(clients)

    with pytest.raises(LLMRateLimitError):
        await client.chat([ChatMessage(role="user", content="hi")])

    assert [c.calls for c in clients] == [1, 1, 1]


async def test_failover_applies_to_vision_too() -> None:
    first = _StubClient("k1", fail=LLMRateLimitError("limited"))
    second = _StubClient("k2")
    client = FailoverLLMClient([first, second])

    result = await client.vision(b"bytes", "prompt")

    assert result.content == "k2"


def test_failover_requires_at_least_one_client() -> None:
    with pytest.raises(ValueError, match="at least one client"):
        FailoverLLMClient([])


def test_get_llm_client_builds_the_whole_chain(make_settings: MakeSettings) -> None:
    settings = make_settings(
        llm_provider="groq",
        groq_api_key=None,
        groq_api_key_1="k1",
        groq_api_key_2="k2",
        groq_api_key_3="k3",
        openrouter_api_key="or-key",
    )
    client = get_llm_client(settings)
    assert isinstance(client, FailoverLLMClient)

    built = [cast("LangChainLLMClient", c) for c in client.clients]
    providers = [c._provider for c in built]  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert providers == ["groq", "groq", "groq", "openrouter"]


def test_openrouter_fallback_does_not_inherit_the_groq_model_override(
    make_settings: MakeSettings,
) -> None:
    settings = make_settings(
        llm_provider="groq",
        groq_api_key="k1",
        openrouter_api_key="or-key",
        llm_model="a-groq-only-model",
    )
    client = get_llm_client(settings)
    assert isinstance(client, FailoverLLMClient)

    groq_client, openrouter_client = (cast("LangChainLLMClient", c) for c in client.clients)
    assert groq_client._model == "a-groq-only-model"  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert openrouter_client._model == OPENROUTER_DEFAULT_MODEL  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


class _RateLimitedChatModel(FakeChatModel):
    """A chat model whose provider call fails the way a 429 does."""

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> LCChatResult:
        del messages, stop, run_manager, kwargs
        raise _FakeRateLimitError("429 rate limit reached; key=should-not-leak")


async def test_chat_maps_a_429_to_llm_rate_limit_error() -> None:
    client = _client(chat_model=_RateLimitedChatModel(), provider="groq")

    with pytest.raises(LLMRateLimitError) as exc_info:
        await client.chat([ChatMessage(role="user", content="hi")])

    # Still provider + exception class name only: the underlying text is
    # inspected to classify the failure but never carried into the message.
    assert str(exc_info.value) == "groq chat call failed: _FakeRateLimitError"
    assert "should-not-leak" not in str(exc_info.value)


# --------------------------------------------------------------------------
# redact_trace_payload (PACKET P-LS2)
# --------------------------------------------------------------------------

_SENTINEL = "zzqxflarp_leak_sentinel_do_not_transmit"


def test_realistic_node_payload_is_redacted_and_sentinel_is_gone() -> None:
    """A shape like what LangGraph would hand `hide_inputs` for the
    `understand_input` node -- nested, and carrying the learner's raw text --
    must come back with its SHAPE intact and the sentinel nowhere in it.

    Updated when blanket redaction was replaced by shape-preserving redaction:
    a trace where every node read `{"redacted": true, "key_count": 1}` was safe
    and useless. Key names are structural (they come from our own schemas);
    values that could carry learner prose become type+size placeholders."""
    node_payload = {
        "input": {
            "text": f"please help me fix this: {_SENTINEL}",
            "language": "python",
            "image": None,
        },
        "normalized": {"text": f"please help me fix this: {_SENTINEL}", "language": "python"},
    }

    result = redact_trace_payload(node_payload)

    shown = f"<str:{len(f'please help me fix this: {_SENTINEL}')}>"
    assert result == {
        "input": {"text": shown, "language": "python", "image": None},
        "normalized": {"text": shown, "language": "python"},
    }
    # The security property is unchanged and is what actually matters here.
    assert _SENTINEL not in str(result)


def test_real_trace_inputs_and_outputs_pass_through_unchanged() -> None:
    """The actual `teaching_graph` parent-run payloads must survive redaction
    byte-for-byte -- that's the entire point of the allow-list."""
    inputs = _trace_inputs(
        RawInput(text="hi"),
        user_id=uuid4(),
        conversation_id=uuid4(),
        max_llm_calls=5,
    )
    outputs = _trace_outputs(
        AgentState(input=RawInput(text="hi")), BudgetedLLMClient(FakeLLMClient(), 5)
    )

    assert redact_trace_payload(inputs) == inputs
    assert redact_trace_payload(outputs) == outputs


def test_allow_listed_keys_with_an_overlong_string_value_are_redacted() -> None:
    """Every key being allow-listed is not enough on its own -- the length
    rule must still do real work against an oversized value."""
    payload = {"route": "x" * 65, "llm_calls": 1}

    result = redact_trace_payload(payload)

    # The oversized value is replaced; the safe one beside it still passes.
    assert result == {"route": "<str:65>", "llm_calls": 1}


def test_allow_listed_keys_with_a_max_length_string_value_pass_through() -> None:
    """The boundary itself: exactly `_MAX_TRACE_SCALAR_CHARS` chars is fine."""
    payload = {"route": "x" * 64, "llm_calls": 1}

    assert redact_trace_payload(payload) == payload


def test_non_dict_empty_dict_and_none_payloads_do_not_raise() -> None:
    assert redact_trace_payload(None) == {"redacted": True, "key_count": 0}
    # An empty payload now summarises to an empty payload -- there is nothing
    # to hide and a marker would be noise.
    assert redact_trace_payload({}) == {}
    assert redact_trace_payload("not a dict") == {"redacted": True, "key_count": 0}
    assert redact_trace_payload(["also", "not", "a", "dict"]) == {
        "redacted": True,
        "key_count": 0,
    }


def test_trace_inputs_and_outputs_keys_are_all_allow_listed() -> None:
    """Guards against the allow-list and `_trace_inputs`/`_trace_outputs`
    drifting apart silently: every key those two functions actually emit must
    already be in `_ALLOWED_TRACE_METADATA_KEYS`, or the parent run's own
    metadata would start being redacted too."""
    inputs = _trace_inputs(
        RawInput(text="hi"),
        user_id=uuid4(),
        conversation_id=uuid4(),
        max_llm_calls=5,
    )
    outputs = _trace_outputs(
        AgentState(input=RawInput(text="hi")), BudgetedLLMClient(FakeLLMClient(), 5)
    )

    assert set(inputs) <= _ALLOWED_TRACE_METADATA_KEYS
    assert set(outputs) <= _ALLOWED_TRACE_METADATA_KEYS


async def test_failover_rewinds_to_the_first_key_after_the_cooldown() -> None:
    """ADAPTIVE-upgrade P4: a per-minute 429 must not pin the process to the
    last fallback forever. Within the window the cursor stays sticky; after it,
    the first credential is tried again (and kept if it works)."""
    now = [0.0]
    first = _StubClient("k1", fail=LLMRateLimitError("limited"))
    second = _StubClient("k2")
    client = FailoverLLMClient([first, second], rewind_after_s=60.0, clock=lambda: now[0])

    await client.chat([ChatMessage(role="user", content="a")])
    now[0] = 30.0
    await client.chat([ChatMessage(role="user", content="b")])
    assert first.calls == 1  # still sticky inside the window

    first.fail = None  # the per-minute limit has reset
    now[0] = 61.0
    result = await client.chat([ChatMessage(role="user", content="c")])
    assert result.content == "k1"
    assert client.active_index == 0


async def test_a_429_on_the_last_key_does_not_postpone_the_rewind() -> None:
    """Code review P4: only an actual cursor move starts the rewind window."""
    now = [0.0]
    first = _StubClient("k1", fail=LLMRateLimitError("limited"))
    last = _StubClient("k2", fail=LLMRateLimitError("limited"))
    client = FailoverLLMClient([first, last], rewind_after_s=60.0, clock=lambda: now[0])
    for at in (0.0, 30.0, 59.0):
        now[0] = at
        with pytest.raises(LLMRateLimitError):
            await client.chat([ChatMessage(role="user", content="x")])
    first.fail = None
    now[0] = 61.0
    result = await client.chat([ChatMessage(role="user", content="y")])
    assert result.content == "k1"


async def test_a_limited_last_fallback_retries_earlier_keys_once() -> None:
    """P5: with the cursor stuck on a 429-ing fallback, an earlier key whose
    per-minute limit has reset still serves the call."""
    first = _StubClient("k1", fail=LLMRateLimitError("limited"))
    last = _StubClient("k2", fail=LLMRateLimitError("limited"))
    client = FailoverLLMClient([first, last], rewind_after_s=3600.0)
    with pytest.raises(LLMRateLimitError):
        await client.chat([ChatMessage(role="user", content="x")])
    assert client.active_index == 1
    first.fail = None
    result = await client.chat([ChatMessage(role="user", content="y")])
    assert result.content == "k1"
    assert client.active_index == 0
