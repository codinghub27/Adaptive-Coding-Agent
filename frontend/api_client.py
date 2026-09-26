"""Thin, typed HTTP client for the Adaptive Coding Tutor backend API.

This is the frontend's *only* point of contact with the backend: every call
here is a plain HTTP request (via `httpx`) against `API_BASE_URL`. This
module must never `import app.*`, touch a database, or execute code -- see
the Phase 08 frontend packet's architectural rule.

Response bodies (chat text, hint bodies, profile error tags, etc.) are
**untrusted, display-only data** produced upstream by the graph/LLM layer;
this client only parses their declared JSON shape and never executes or
interprets their contents.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, TypedDict, cast

import httpx

__all__ = [
    "CODE_SECTION_KINDS",
    "ApiClient",
    "ApiError",
    "ChatResponseDict",
    "ConversationDict",
    "DoneEvent",
    "ErrorEvent",
    "GeneratedResponseDict",
    "LoginResultDict",
    "PlanDict",
    "ProfileDict",
    "RegisterResultDict",
    "ResponseSectionDict",
    "StageEvent",
    "StreamEvent",
]

DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"

#: Section kinds that carry full solution code -- mirrors
#: `app.schemas.response.CODE_SECTION_KINDS` on the API side. Duplicated
#: (rather than imported) because `frontend/` must never `import app.*`.
CODE_SECTION_KINDS: frozenset[str] = frozenset({"code", "patch"})

#: Fixed, safe message shown for any transport-level failure (connection
#: refused, timeout, DNS, ...) -- never the raw exception text.
_UNREACHABLE_DETAIL = "could not reach the API"

#: Fixed, safe fallback when a non-2xx response has no usable JSON `detail`.
_DEFAULT_ERROR_DETAIL = "the request could not be completed"


def api_base_url() -> str:
    """Return the configured API base URL, defaulting to the local dev server."""
    return os.environ.get("API_BASE_URL", DEFAULT_API_BASE_URL)


class ApiError(Exception):
    """Raised for any non-2xx API response or transport-level failure.

    `status_code` is `0` for a transport failure (no response was ever
    received). `detail` is always a short, safe, display-only string --
    never a raw traceback or the underlying exception's full text.
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class RegisterResultDict(TypedDict):
    id: str
    username: str


class LoginResultDict(TypedDict):
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int


class ConversationDict(TypedDict):
    conversation_id: str
    title: str | None


class ProfileDict(TypedDict):
    language: str | None
    skill_levels: dict[str, float]
    learning_preferences: dict[str, bool]
    common_errors: list[str]


class ResponseSectionDict(TypedDict):
    kind: str
    title: str
    body: str
    language: str | None


class GeneratedResponseDict(TypedDict):
    text: str
    sections: list[ResponseSectionDict]
    assistance_level: str
    hint_level: int | None
    hint_ceiling: int | None
    more_help_available: bool
    reveals_code: bool
    next_steps: list[str]
    citations: list[str]


class PlanDict(TypedDict, total=False):
    difficulty: str
    assistance_level: str
    solution_strategy: str
    topic: str | None
    skill_level: float
    step_by_step: bool
    concise: bool
    watch_errors: list[str]
    rationale: list[str]


class ChatResponseDict(TypedDict):
    response: str
    route: str
    intent: dict[str, Any] | None
    plan: PlanDict | None
    verification: dict[str, Any] | None
    events: list[dict[str, Any]]
    events_persisted: list[str]
    errors: list[dict[str, Any]]
    llm_calls: int
    generated: GeneratedResponseDict | None
    conversation_id: str | None


@dataclass(frozen=True)
class StageEvent:
    """One `event: stage` frame from `POST /chat/stream`."""

    node: str
    label: str


@dataclass(frozen=True)
class DoneEvent:
    """The terminal `event: done` frame: `data` is a `ChatResponseDict`."""

    data: ChatResponseDict


@dataclass(frozen=True)
class ErrorEvent:
    """The terminal `event: error` frame."""

    detail: str


StreamEvent = StageEvent | DoneEvent | ErrorEvent

_HttpFiles = dict[str, tuple[str, bytes, str]]


def _build_form_data(
    text: str | None,
    language: str | None,
    conversation_id: str | None,
    topic: str | None,
) -> dict[str, str]:
    """Build the multipart form fields shared by `send_chat`/`stream_chat`.

    Only fields the caller actually supplied are included -- the `/chat`
    endpoints treat every field as optional (`Form(...) = None`).
    """
    data: dict[str, str] = {}
    if text is not None:
        data["text"] = text
    if language is not None:
        data["language"] = language
    if conversation_id is not None:
        data["conversation_id"] = conversation_id
    if topic is not None:
        data["topic"] = topic
    return data


def _build_files(
    image: bytes | None, image_filename: str, image_content_type: str
) -> _HttpFiles | None:
    if image is None:
        return None
    return {"image": (image_filename, image, image_content_type)}


def _error_detail_from_response(response: httpx.Response) -> str:
    try:
        raw_body: Any = response.json()
    except ValueError:
        return _DEFAULT_ERROR_DETAIL
    if isinstance(raw_body, dict):
        body = cast("dict[str, Any]", raw_body)
        detail = body.get("detail")
        if isinstance(detail, str) and detail:
            return detail
    return _DEFAULT_ERROR_DETAIL


def _parse_sse_frame(event_name: str, raw_data: str) -> StreamEvent:
    """Parse one already-split SSE `event`/`data` pair into a `StreamEvent`.

    Any malformed frame degrades to an `ErrorEvent` rather than raising --
    the UI must never crash on a stream hiccup.
    """
    try:
        raw_payload: Any = json.loads(raw_data) if raw_data else {}
    except ValueError:
        return ErrorEvent(detail=_DEFAULT_ERROR_DETAIL)

    if not isinstance(raw_payload, dict):
        return ErrorEvent(detail=_DEFAULT_ERROR_DETAIL)
    payload = cast("dict[str, Any]", raw_payload)

    if event_name == "stage":
        node = payload.get("node")
        label = payload.get("label")
        if isinstance(node, str) and isinstance(label, str):
            return StageEvent(node=node, label=label)
        return ErrorEvent(detail=_DEFAULT_ERROR_DETAIL)

    if event_name == "done":
        return DoneEvent(data=cast("ChatResponseDict", payload))

    if event_name == "error":
        detail = payload.get("detail")
        return ErrorEvent(detail=detail if isinstance(detail, str) else _DEFAULT_ERROR_DETAIL)

    return ErrorEvent(detail=_DEFAULT_ERROR_DETAIL)


class ApiClient:
    """Thin HTTP client for the backend. Holds no auth state itself --
    callers pass `access_token` explicitly (kept in `st.session_state`)."""

    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or api_base_url()).rstrip("/")
        #: Short timeout for the quick calls (auth, profile, new conversation).
        self._timeout = timeout
        #: A chat turn is not a quick call: it can spend several LLM calls plus
        #: a Docker sandbox run, easily exceeding 30s. Timing out here is worse
        #: than slow -- the backend finishes and COMMITS the turn regardless, so
        #: the UI would show a failure for a turn that actually succeeded, and
        #: the next "Show me the next hint" would resend against a ladder the
        #: server had already advanced. Generous connect/write bounds still
        #: catch a genuinely unreachable API; `read=None` waits for the turn.
        self._turn_timeout = httpx.Timeout(timeout, read=None)

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        raise ApiError(response.status_code, _error_detail_from_response(response))

    def _auth_headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    def register(self, username: str, password: str) -> RegisterResultDict:
        """`POST /auth/register`."""
        try:
            response = httpx.post(
                f"{self.base_url}/auth/register",
                json={"username": username, "password": password},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, _UNREACHABLE_DETAIL) from exc
        self._raise_for_status(response)
        return cast("RegisterResultDict", response.json())

    def login(self, username: str, password: str) -> LoginResultDict:
        """`POST /auth/login`."""
        try:
            response = httpx.post(
                f"{self.base_url}/auth/login",
                json={"username": username, "password": password},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, _UNREACHABLE_DETAIL) from exc
        self._raise_for_status(response)
        return cast("LoginResultDict", response.json())

    def create_conversation(
        self, access_token: str, title: str | None = None
    ) -> ConversationDict:
        """`POST /conversations`."""
        try:
            response = httpx.post(
                f"{self.base_url}/conversations",
                json={"title": title},
                headers=self._auth_headers(access_token),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, _UNREACHABLE_DETAIL) from exc
        self._raise_for_status(response)
        return cast("ConversationDict", response.json())

    def get_profile(self, access_token: str) -> ProfileDict:
        """`GET /profile`."""
        try:
            response = httpx.get(
                f"{self.base_url}/profile",
                headers=self._auth_headers(access_token),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, _UNREACHABLE_DETAIL) from exc
        self._raise_for_status(response)
        return cast("ProfileDict", response.json())

    def send_chat(
        self,
        access_token: str,
        *,
        text: str | None = None,
        language: str | None = None,
        image: bytes | None = None,
        image_filename: str = "image.png",
        image_content_type: str = "image/png",
        conversation_id: str | None = None,
        topic: str | None = None,
    ) -> ChatResponseDict:
        """`POST /chat` (non-streaming)."""
        data = _build_form_data(text, language, conversation_id, topic)
        files = _build_files(image, image_filename, image_content_type)
        try:
            response = httpx.post(
                f"{self.base_url}/chat",
                data=data,
                files=files,
                headers=self._auth_headers(access_token),
                timeout=self._turn_timeout,
            )
        except httpx.HTTPError as exc:
            raise ApiError(0, _UNREACHABLE_DETAIL) from exc
        self._raise_for_status(response)
        return cast("ChatResponseDict", response.json())

    def stream_chat(
        self,
        access_token: str,
        *,
        text: str | None = None,
        language: str | None = None,
        image: bytes | None = None,
        image_filename: str = "image.png",
        image_content_type: str = "image/png",
        conversation_id: str | None = None,
        topic: str | None = None,
    ) -> Iterator[StreamEvent]:
        """`POST /chat/stream`: yields `StreamEvent`s parsed from the SSE body.

        Minimal hand-rolled SSE parsing (no new dependency): frames are
        separated by a blank line; each frame has an `event:` line and one
        or more `data:` lines. Exactly one terminal `DoneEvent`/`ErrorEvent`
        is expected from the server, but a transport failure mid-stream is
        also surfaced as an `ErrorEvent` rather than propagating a raw
        exception into the UI.
        """
        data = _build_form_data(text, language, conversation_id, topic)
        files = _build_files(image, image_filename, image_content_type)
        headers = self._auth_headers(access_token)

        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/chat/stream",
                data=data,
                files=files,
                headers=headers,
                timeout=self._turn_timeout,
            ) as response:
                if not response.is_success:
                    response.read()
                    yield ErrorEvent(detail=_error_detail_from_response(response))
                    return

                event_name: str | None = None
                data_lines: list[str] = []
                for line in response.iter_lines():
                    if line == "":
                        if event_name is not None:
                            yield _parse_sse_frame(event_name, "\n".join(data_lines))
                        event_name = None
                        data_lines = []
                        continue
                    if line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:") :].strip())

                if event_name is not None:
                    yield _parse_sse_frame(event_name, "\n".join(data_lines))
        except httpx.HTTPError:
            yield ErrorEvent(detail=_UNREACHABLE_DETAIL)
