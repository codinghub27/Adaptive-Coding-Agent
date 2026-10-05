"""The optional classifier model, SDK retries under failover, and HTTP telemetry."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx
from langchain_groq import ChatGroq

from app.config import Settings
from app.llm.base import ChatMessage
from app.llm.client import (
    FailoverLLMClient,
    LangChainLLMClient,
    SmallTaskLLMClient,
    get_llm_client,
)
from app.llm.telemetry import HttpTelemetry
from tests.input.fakes import FakeLLMClient

_CLASSIFY = "You are the intent classifier."


def _ask(system: str) -> list[ChatMessage]:
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content="x")]


async def test_only_the_named_task_goes_to_the_small_model() -> None:
    main, small = FakeLLMClient(chat_content="main"), FakeLLMClient(chat_content="small")
    client = SmallTaskLLMClient(main, small, [_CLASSIFY])
    assert (await client.chat(_ask(_CLASSIFY))).content == "small"
    assert (await client.chat(_ask("You are the DSA solver."))).content == "main"
    assert len(small.chat_calls) == 1
    assert len(main.chat_calls) == 1


async def test_the_small_model_failing_falls_back_to_the_main_one() -> None:
    main, small = FakeLLMClient(chat_content="main"), FakeLLMClient(raise_chat=True)
    client = SmallTaskLLMClient(main, small, [_CLASSIFY])
    assert (await client.chat(_ask(_CLASSIFY))).content == "main"


def _chat_model(client: object) -> ChatGroq:
    assert isinstance(client, LangChainLLMClient)
    model = client._chat_model  # pyright: ignore[reportPrivateUsage]
    assert isinstance(model, ChatGroq)
    return model


def test_sdk_retries_are_off_only_when_there_is_another_credential(
    make_settings: Callable[..., Settings],
) -> None:
    """With a credential to fail over to, the SDK sleeping `retry-after` on a
    429 is pure delay. With one credential, its retries are all there is."""
    single = get_llm_client(
        make_settings(llm_provider="groq", groq_api_key="k0", openrouter_api_key=None)
    )
    assert _chat_model(single).max_retries == 2

    chain = get_llm_client(
        make_settings(llm_provider="groq", groq_api_key="k0", groq_api_key_1="k1")
    )
    assert isinstance(chain, FailoverLLMClient)
    groq_models = [
        c._chat_model  # pyright: ignore[reportPrivateUsage]
        for c in chain.clients
        if isinstance(c, LangChainLLMClient)
    ]
    retries = [m.max_retries for m in groq_models if isinstance(m, ChatGroq)]
    assert retries == [0, 0]


def test_the_classifier_model_is_opt_in(make_settings: Callable[..., Settings]) -> None:
    off = get_llm_client(
        make_settings(llm_provider="groq", groq_api_key="k0", groq_api_key_1="k1"),
        small_task_prompts=(_CLASSIFY,),
    )
    assert not isinstance(off, SmallTaskLLMClient)
    on = get_llm_client(
        make_settings(
            llm_provider="groq",
            groq_api_key="k0",
            groq_api_key_1="k1",
            llm_classifier_model="openai/gpt-oss-20b",
        ),
        small_task_prompts=(_CLASSIFY,),
    )
    assert isinstance(on, SmallTaskLLMClient)


async def test_telemetry_records_metadata_and_never_the_key_or_the_body(tmp_path: Path) -> None:
    log = tmp_path / "http.jsonl"
    telemetry = HttpTelemetry(provider="groq", credential_index=3, log_path=log)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            429,
            headers={
                "retry-after": "12",
                "x-ratelimit-remaining-tokens": "73",
                "x-ratelimit-reset-tokens": "37.8s",
                "set-cookie": "session=secret",
            },
            json={"error": "rate limited: SECRET-BODY"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        event_hooks={"request": [telemetry.on_request], "response": [telemetry.on_response]},
    ) as client:
        await client.post(
            "https://api.example.test/openai/v1/chat/completions",
            headers={"Authorization": "Bearer SECRET-KEY"},
            json={"messages": [{"role": "user", "content": "SECRET-PROMPT"}]},
        )

    text = log.read_text(encoding="utf-8")
    record = json.loads(text)
    assert record["status"] == 429
    assert record["credential"] == 3
    assert record["path"] == "/openai/v1/chat/completions"
    assert record["retry-after"] == "12"
    assert record["x-ratelimit-remaining-tokens"] == "73"
    assert isinstance(record["seconds"], float)
    assert "SECRET" not in text
    assert "set-cookie" not in text
