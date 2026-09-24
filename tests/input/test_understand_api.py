"""Tests for `POST /understand` (`app.input.api`).

The app is built via `create_app()` without running its lifespan (no real
DB/Qdrant/LLM connections); `get_llm` is overridden with a `FakeLLMClient` so
every test is fully offline.
"""

import io
import json
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from starlette.types import Message, Receive, Scope, Send

from app.auth.deps import get_current_user
from app.config import Settings
from app.input.api import (
    BodySizeLimitMiddleware,
    _combine,  # pyright: ignore[reportPrivateUsage]
    get_llm,
)
from app.input.normalize import MAX_TEXT_CHARS
from app.input.vision import MAX_IMAGE_BYTES
from app.main import create_app
from app.schemas.auth import AuthUser
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]

_TEST_USER = AuthUser(id=uuid4(), handle="test-user", session_id=uuid4())


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


@asynccontextmanager
async def _client_for(
    make_settings: MakeSettings, fake: FakeLLMClient
) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_app(make_settings())
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_current_user] = lambda: _TEST_USER
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# --------------------------------------------------------------------------
# text-only
# --------------------------------------------------------------------------


async def test_text_only_code_and_indexerror_returns_debug_intent(
    make_settings: MakeSettings,
) -> None:
    fake = FakeLLMClient(chat_content="unused")
    async with _client_for(make_settings, fake) as client:
        text = (
            "```python\n"
            "nums = [1, 2, 3]\n"
            "print(nums[5])\n"
            "```\n"
            "Traceback (most recent call last):\n"
            "IndexError: list index out of range\n"
        )
        response = await client.post("/understand", data={"text": text})

    assert response.status_code == 200
    body = response.json()
    assert body["input"]["source"] == "text"
    assert len(body["input"]["code"]) == 1
    assert body["input"]["error"] is not None
    assert body["intent"]["intent"] == "CODE_DEBUG"
    assert body["intent"]["source"] == "rule"
    assert "low_confidence" in body["intent"]
    assert fake.chat_calls == []


# --------------------------------------------------------------------------
# image-only
# --------------------------------------------------------------------------


async def test_image_only_extracts_problem_and_classifies_dsa_solve(
    make_settings: MakeSettings,
) -> None:
    vision_json = (
        '{"problem": "Two Sum: given an array of integers, return indices that sum '
        'to a target value.", "code": null, "code_language": null, "error": null, '
        '"constraints": ["2 <= nums.length <= 10^4"], "question": null}'
    )
    fake = FakeLLMClient(vision_content=vision_json, chat_content="unused")
    async with _client_for(make_settings, fake) as client:
        files = {"image": ("problem.png", _png_bytes(), "image/png")}
        response = await client.post("/understand", files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["input"]["source"] == "image"
    assert body["input"]["problem"] is not None
    assert body["input"]["constraints"] == ["2 <= nums.length <= 10^4"]
    assert body["intent"]["intent"] == "DSA_SOLVE"


# --------------------------------------------------------------------------
# text + image merged
# --------------------------------------------------------------------------


async def test_text_and_image_merge_question_from_text(make_settings: MakeSettings) -> None:
    vision_json = (
        '{"problem": "Reverse a linked list in place.", "code": null, '
        '"code_language": null, "error": null, "constraints": [], "question": null}'
    )
    chat_json = '{"intent": "APPROACH_DISCUSSION", "confidence": 0.65, "rationale": "asks how"}'
    fake = FakeLLMClient(vision_content=vision_json, chat_content=chat_json)
    async with _client_for(make_settings, fake) as client:
        files = {"image": ("problem.png", _png_bytes(), "image/png")}
        data = {"text": "How should I approach this?"}
        response = await client.post("/understand", data=data, files=files)

    assert response.status_code == 200
    body = response.json()
    assert body["input"]["source"] == "image"
    assert body["input"]["question"] == "How should I approach this?"
    assert body["input"]["problem"] == "Reverse a linked list in place."
    assert body["intent"]["source"] == "llm"
    assert len(fake.chat_calls) == 1


# --------------------------------------------------------------------------
# validation failures
# --------------------------------------------------------------------------


async def test_no_text_and_no_image_returns_422(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        response = await client.post("/understand", data={})

    assert response.status_code == 422
    assert response.json() == {"detail": "provide text and/or an image"}


async def test_non_image_bytes_as_image_returns_415(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        files = {"image": ("junk.png", b"just some plain bytes, not an image", "image/png")}
        response = await client.post("/understand", files=files)

    assert response.status_code == 415
    assert fake.vision_calls == []


async def test_oversize_image_returns_413(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        oversized = _png_bytes() + b"\x00" * (MAX_IMAGE_BYTES + 1)
        files = {"image": ("big.png", oversized, "image/png")}
        response = await client.post("/understand", files=files)

    assert response.status_code == 413
    assert fake.vision_calls == []


async def test_vision_llm_error_returns_502_without_exception_text(
    make_settings: MakeSettings,
) -> None:
    fake = FakeLLMClient(raise_vision=True)
    async with _client_for(make_settings, fake) as client:
        files = {"image": ("problem.png", _png_bytes(), "image/png")}
        response = await client.post("/understand", files=files)

    assert response.status_code == 502
    body = response.json()
    assert body == {"detail": "vision extraction failed"}
    assert "RuntimeError" not in response.text


async def test_text_over_max_chars_returns_413(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        response = await client.post("/understand", data={"text": "a" * (MAX_TEXT_CHARS + 1)})

    assert response.status_code == 413


async def test_empty_image_returns_422(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        response = await client.post(
            "/understand", files={"image": ("empty.png", b"", "image/png")}
        )

    assert response.status_code == 422


# --------------------------------------------------------------------------
# language form field (capped length, validated shape)
# --------------------------------------------------------------------------


async def test_language_field_too_long_returns_422(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient()
    async with _client_for(make_settings, fake) as client:
        response = await client.post(
            "/understand", data={"text": "some code text here", "language": "x" * 33}
        )

    assert response.status_code == 422


async def test_invalid_language_field_is_ignored_not_errored(make_settings: MakeSettings) -> None:
    chat_json = '{"intent": "CONCEPT_EXPLANATION", "confidence": 0.5, "rationale": "n/a"}'
    fake = FakeLLMClient(chat_content=chat_json)
    async with _client_for(make_settings, fake) as client:
        response = await client.post(
            "/understand", data={"text": "What is recursion?", "language": "not valid!"}
        )

    assert response.status_code == 200
    assert response.json()["input"]["language"] is None


async def test_valid_language_field_is_used_as_hint(make_settings: MakeSettings) -> None:
    fake = FakeLLMClient(chat_content="unused")
    async with _client_for(make_settings, fake) as client:
        response = await client.post(
            "/understand", data={"text": "why does this fail?", "language": "py"}
        )

    assert response.status_code == 200
    assert response.json()["input"]["language"] == "python"


# --------------------------------------------------------------------------
# _combine: no assert in the request path
# --------------------------------------------------------------------------


def test_combine_raises_runtime_error_instead_of_asserting_when_both_none() -> None:
    with pytest.raises(RuntimeError):
        _combine(None, None)


# --------------------------------------------------------------------------
# BodySizeLimitMiddleware: pure-ASGI request body size cap
# --------------------------------------------------------------------------


def _collect_response(sent: list[Message]) -> tuple[int, bytes]:
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


async def test_middleware_rejects_oversized_content_length_without_calling_app() -> None:
    app_called = False

    async def downstream_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal app_called
        app_called = True

    middleware = BodySizeLimitMiddleware(downstream_app, max_bytes=100)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/understand",
        "headers": [(b"content-length", b"1000")],
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)

    assert app_called is False
    status, body = _collect_response(sent)
    assert status == 413
    assert json.loads(body) == {"detail": "request body too large"}


async def test_middleware_aborts_chunked_body_once_over_limit_without_content_length() -> None:
    async def downstream_app(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break

    middleware = BodySizeLimitMiddleware(downstream_app, max_bytes=10)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/understand",
        "headers": [],
    }
    chunks = iter([b"12345", b"67890", b"more-bytes-past-the-limit"])

    async def receive() -> Message:
        chunk = next(chunks, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)

    status, body = _collect_response(sent)
    assert status == 413
    assert json.loads(body) == {"detail": "request body too large"}


async def test_middleware_passes_through_normal_sized_request() -> None:
    app_called = False

    async def downstream_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal app_called
        app_called = True
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodySizeLimitMiddleware(downstream_app, max_bytes=1000)
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": "/understand",
        "headers": [(b"content-length", b"5")],
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"small", "more_body": False}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)

    assert app_called is True
    status, body = _collect_response(sent)
    assert status == 200
    assert body == b"ok"


async def test_middleware_ignores_non_understand_paths() -> None:
    app_called = False

    async def downstream_app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal app_called
        app_called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodySizeLimitMiddleware(downstream_app, max_bytes=1)
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "headers": [(b"content-length", b"999999")],
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)

    assert app_called is True
    status, _ = _collect_response(sent)
    assert status == 200


async def test_full_app_health_endpoint_unaffected_by_body_size_middleware(
    make_settings: MakeSettings,
) -> None:
    fake = FakeLLMClient()
    app = create_app(make_settings())
    app.dependency_overrides[get_llm] = lambda: fake
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")

    # Postgres/Qdrant aren't actually reachable in this unit test, so
    # /health itself reports "degraded" (503) -- what matters here is that
    # the body-size-limit middleware didn't intercept a bodyless GET.
    assert response.status_code != 413
