"""Tests for `POST /chat/stream` (`app.graph.api.chat_stream`).

Mirrors `tests/graph/test_chat_api.py`'s style: the app is built via
`create_app()` without running its lifespan, `get_llm` overridden with a
`FakeLLMClient`, `get_session` overridden so `get_current_user`'s own
dependency chain stays offline, and `get_current_user` overridden with a
fixed `AuthUser` where auth isn't itself under test. `/chat/stream` reads
its own session from `app.state.session_factory` (never the `get_session`
dependency -- see `app.graph.api._get_session_factory`), so streaming tests
set `app.state.session_factory` directly rather than overriding a dependency.
"""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from app.auth.deps import get_app_settings, get_current_user
from app.db.session import get_session
from app.graph.api import STREAM_ERROR_DETAIL
from app.input.api import get_llm
from app.main import create_app
from app.schemas.auth import AuthUser
from tests.input.fakes import FakeLLMClient

_DEBUG_TEXT = (
    "```python\n"
    "def get_item(items, idx):\n"
    "    return items[idx]\n"
    "```\n"
    "\n"
    "IndexError: list index out of range\n"
)

_DEFAULT_TEST_USER = AuthUser(id=uuid4(), handle="test-user", session_id=uuid4())


class _EmptyResult:
    def scalar_one_or_none(self) -> None:
        return None

    def scalars(self) -> list[Any]:
        return []


class _NoOpNestedTransaction:
    async def __aenter__(self) -> "_NoOpNestedTransaction":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeSession:
    """A minimal stand-in for `AsyncSession`, usable both as the value yielded
    by `get_session` and as what `session_factory()` returns from an `async
    with` block (mirrors `_StubSession` in `tests/graph/test_chat_api.py`,
    plus the async context manager protocol `async_sessionmaker()` instances
    support)."""

    def __init__(self, *, fail_commit: bool = False) -> None:
        self.committed = False
        self.rolled_back = False
        self.fail_commit = fail_commit

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def commit(self) -> None:
        if self.fail_commit:
            raise RuntimeError("simulated commit failure")
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def execute(self, *args: object, **kwargs: object) -> _EmptyResult:
        del args, kwargs
        return _EmptyResult()

    def begin_nested(self) -> _NoOpNestedTransaction:
        return _NoOpNestedTransaction()


class _FakeSessionFactory:
    """Stand-in for `async_sessionmaker[AsyncSession]`: calling it returns a
    fresh (or shared, for assertion purposes) `_FakeSession`."""

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    def __call__(self) -> _FakeSession:
        return self._session


async def _stub_session() -> AsyncIterator[Any]:
    yield _FakeSession()


def _build_app(
    make_settings: Any,
    fake_llm: FakeLLMClient,
    *,
    session_factory: _FakeSessionFactory | None = None,
    current_user: AuthUser = _DEFAULT_TEST_USER,
) -> httpx.ASGITransport:
    app = create_app(make_settings())
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_current_user] = lambda: current_user
    if session_factory is not None:
        app.state.session_factory = session_factory
    return httpx.ASGITransport(app=app)


async def _collect_sse_frames(response: httpx.Response) -> list[tuple[str, str]]:
    """Parse an SSE body already read into `response.text` into `(event, data)` pairs."""
    frames: list[tuple[str, str]] = []
    event: str | None = None
    data_lines: list[str] = []
    for line in response.text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        elif line == "" and event is not None:
            frames.append((event, "\n".join(data_lines)))
            event = None
            data_lines = []
    if event is not None:
        frames.append((event, "\n".join(data_lines)))
    return frames


async def test_chat_stream_done_frame_matches_post_chat_body(
    make_settings: Any,
) -> None:
    fake_session = _FakeSession()
    fake = FakeLLMClient(chat_content="unused")

    transport_stream = _build_app(
        make_settings, fake, session_factory=_FakeSessionFactory(fake_session)
    )
    async with httpx.AsyncClient(transport=transport_stream, base_url="http://test") as client:
        response = await client.post("/chat/stream", data={"text": _DEBUG_TEXT})
    assert response.status_code == 200
    frames = await _collect_sse_frames(response)
    done_frames = [data for event, data in frames if event == "done"]
    error_frames = [data for event, data in frames if event == "error"]
    assert error_frames == []
    assert len(done_frames) == 1
    streamed_body = json.loads(done_frames[0])

    fake2 = FakeLLMClient(chat_content="unused")
    transport_plain = _build_app(make_settings, fake2)
    async with httpx.AsyncClient(transport=transport_plain, base_url="http://test") as client:
        plain_response = await client.post("/chat", data={"text": _DEBUG_TEXT})
    assert plain_response.status_code == 200

    assert streamed_body == plain_response.json()
    assert fake_session.committed is True


async def test_chat_stream_stage_frames_never_contain_submitted_text(
    make_settings: Any,
) -> None:
    marker = "zzz_super_distinctive_marker_qux_12345"
    text = _DEBUG_TEXT + f"\n# {marker}\n"
    fake_session = _FakeSession()
    fake = FakeLLMClient(chat_content="unused")

    transport = _build_app(make_settings, fake, session_factory=_FakeSessionFactory(fake_session))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat/stream", data={"text": text})

    assert response.status_code == 200
    frames = await _collect_sse_frames(response)
    stage_frames = [data for event, data in frames if event == "stage"]
    assert stage_frames  # sanity: at least one stage event fired
    for data in stage_frames:
        assert marker not in data


async def test_chat_stream_requires_authentication(make_settings: Any) -> None:
    settings = make_settings()
    app = create_app(settings)
    app.dependency_overrides[get_llm] = lambda: FakeLLMClient()
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_app_settings] = lambda: settings
    # get_current_user intentionally left un-overridden.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat/stream", data={"text": _DEBUG_TEXT})

    assert response.status_code == 401


async def test_chat_stream_rejects_invalid_bearer_token(make_settings: Any) -> None:
    settings = make_settings()
    app = create_app(settings)
    app.dependency_overrides[get_llm] = lambda: FakeLLMClient()
    app.dependency_overrides[get_session] = _stub_session
    app.dependency_overrides[get_app_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/chat/stream",
            data={"text": _DEBUG_TEXT},
            headers={"Authorization": "Bearer not-a-real-token"},
        )

    assert response.status_code == 401


async def test_chat_stream_emits_error_frame_with_fixed_message_on_graph_failure(
    make_settings: Any,
) -> None:
    fake_session = _FakeSession(fail_commit=True)
    fake = FakeLLMClient(chat_content="unused")

    transport = _build_app(make_settings, fake, session_factory=_FakeSessionFactory(fake_session))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/chat/stream", data={"text": _DEBUG_TEXT})

    assert response.status_code == 200
    frames = await _collect_sse_frames(response)
    done_frames = [data for event, data in frames if event == "done"]
    error_frames = [data for event, data in frames if event == "error"]
    assert done_frames == []
    assert len(error_frames) == 1
    payload = json.loads(error_frames[0])
    assert payload == {"detail": STREAM_ERROR_DETAIL}
    assert "simulated commit failure" not in error_frames[0]
    assert fake_session.rolled_back is True
