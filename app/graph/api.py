"""`POST /chat` — run one turn of the teaching graph over text and/or an image.

Mirrors `app.input.api`'s `/understand` request shape and validation (same
size/type limits, same "text and/or image" requirement) but drives the full
`app.graph.build.run_graph` pipeline instead of only normalization + intent
classification, and persists this turn's conversation/learning-event state
when `conversation_id` is supplied.

All request content (text, code, error text, image bytes) is **untrusted
user data**; it is only ever passed into the graph, never logged, and never
echoed back inside error details. The acting user comes solely from the
authenticated bearer token (`get_current_user`); there is no caller-supplied
`user_id`. A `conversation_id` owned by a different user is not a data leak:
`app.memory.conversation.get_owned_conversation` (Phase 03) rejects it, which
surfaces here as a `NodeError` from `load_learner_profile`/
`update_learner_model` and an empty/degraded turn, never another user's data.
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth.deps import get_current_user
from app.config import Settings
from app.db.session import get_session
from app.execution.base import CodeRunner
from app.graph.build import GraphRunResult, GraphStageEvent, run_graph, stream_graph
from app.graph.nodes import SAFE_FALLBACK_RESPONSE
from app.graph.state import NodeError, RawInput, RouteKey
from app.input.api import (
    IMAGE_VALIDATION_DETAIL,
    IMAGE_VALIDATION_STATUS,
    get_llm,
    valid_language_hint,
    validate_request,
)
from app.input.normalize import MAX_TEXT_CHARS
from app.input.vision import MAX_IMAGE_BYTES, ImageValidationError, validate_image
from app.knowledge.base import DEFAULT_KNOWLEDGE_TOP_K, Retriever
from app.llm.base import LLMClient
from app.schemas.auth import AuthUser
from app.schemas.base import APIModel
from app.schemas.event import LearningEventCreate
from app.schemas.execution import Verdict
from app.schemas.intent import IntentResult
from app.schemas.plan import ASSISTANCE_ORDER, TeachingPlan
from app.schemas.response import GeneratedResponse

__all__ = ["STREAM_ERROR_DETAIL", "ChatResponse", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

#: Fixed, safe error detail for `/chat/stream`'s terminal `error` frame --
#: never the raw exception text, which may carry secrets or untrusted input
#: (mirrors `NodeError.message`'s convention).
STREAM_ERROR_DETAIL = "the request could not be completed"


class ChatResponse(APIModel):
    """Response body for `POST /chat`: this turn's outcome from the teaching graph."""

    response: str
    route: RouteKey
    intent: IntentResult | None
    plan: TeachingPlan | None
    verification: Verdict | None
    events: list[LearningEventCreate]
    events_persisted: list[UUID]
    errors: list[NodeError]
    llm_calls: int
    generated: GeneratedResponse | None
    conversation_id: UUID | None


def _get_runner(request: Request) -> CodeRunner | None:
    """Return the shared `CodeRunner` configured on `app.state`, if any.

    `app.state.runner` is `None` whenever the sandbox is disabled or was
    unavailable at startup (see `app.main`'s lifespan) -- that's a normal,
    expected state, not an error, so this never raises. `CodeRunner` is a
    `@runtime_checkable` `Protocol`, so `isinstance` is used directly rather
    than `getattr` + a cast, mirroring `_get_retriever` below.
    """
    runner = getattr(request.app.state, "runner", None)
    if isinstance(runner, CodeRunner):
        return runner
    return None


def _get_retriever(request: Request) -> Retriever | None:
    """Return the shared `Retriever` configured on `app.state`, if any.

    `app.state.retriever` is `None` whenever knowledge retrieval is disabled
    or failed to load at startup (see `app.main`'s lifespan) -- that's a
    normal, expected state, not an error, so (unlike `get_llm`) this never
    raises. `Retriever` is a `@runtime_checkable` `Protocol`, so `isinstance`
    is used directly rather than `getattr` + a cast.
    """
    retriever = getattr(request.app.state, "retriever", None)
    if isinstance(retriever, Retriever):
        return retriever
    return None


def _get_knowledge_top_k(request: Request) -> int:
    """Return `settings.knowledge_top_k` from `app.state`, defaulting to
    `DEFAULT_KNOWLEDGE_TOP_K` (the same default `Settings.knowledge_top_k`
    itself uses) when `app.state.settings` isn't configured (as in tests that
    build the app without running its lifespan)."""
    settings = getattr(request.app.state, "settings", None)
    if isinstance(settings, Settings):
        return settings.knowledge_top_k
    return DEFAULT_KNOWLEDGE_TOP_K


async def _read_image(image: UploadFile) -> bytes:
    """Bounded read of `image`, validated (magic bytes, size) before use."""
    data = await image.read(MAX_IMAGE_BYTES + 1)
    try:
        validate_image(data, image.content_type)
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=IMAGE_VALIDATION_STATUS[exc.reason],
            detail=IMAGE_VALIDATION_DETAIL[exc.reason],
        ) from None
    return data


async def _build_raw_input(
    text: str | None,
    language: str | None,
    image: UploadFile | None,
    topic: str | None,
    assistance_cap: str | None = None,
) -> RawInput:
    """Shared request parsing/validation for `/chat` and `/chat/stream`: same
    size/type checks, same "text and/or image" requirement, so the two
    handlers cannot diverge."""
    validate_request(text, image)

    if text is not None and len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="text exceeds maximum length")

    if assistance_cap is not None and assistance_cap not in ASSISTANCE_ORDER:
        # Never echo the submitted value back in the detail.
        raise HTTPException(status_code=422, detail="invalid assistance_cap")

    image_bytes = await _read_image(image) if image is not None else None

    return RawInput(
        text=text,
        language=valid_language_hint(language),
        image=image_bytes,
        image_mime=image.content_type if image is not None else None,
        topic_hint=topic,
        assistance_cap=assistance_cap,
    )


def _to_chat_response(result: GraphRunResult, conversation_id: UUID | None) -> ChatResponse:
    """Assemble the `ChatResponse` body shared verbatim by `/chat` and the
    `done` frame of `/chat/stream`."""
    state = result.state
    return ChatResponse(
        response=state.response if state.response is not None else SAFE_FALLBACK_RESPONSE,
        route=state.route if state.route is not None else "clarify",
        intent=state.intent,
        plan=state.plan,
        verification=state.verification,
        events=state.events,
        events_persisted=state.events_persisted,
        errors=state.errors,
        llm_calls=result.llm_calls,
        generated=state.generated_response,
        conversation_id=conversation_id,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: Request,
    llm: Annotated[LLMClient, Depends(get_llm)],
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    text: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form(max_length=32)] = None,
    image: Annotated[UploadFile | None, File()] = None,
    conversation_id: Annotated[UUID | None, Form()] = None,
    topic: Annotated[str | None, Form(max_length=64)] = None,
    assistance_cap: Annotated[str | None, Form(max_length=16)] = None,
) -> ChatResponse:
    """Run one turn of the teaching graph over `text`/`image` and return its outcome.

    The acting user is always `current_user.id`, resolved from the bearer
    access token by `get_current_user` -- there is no caller-supplied
    `user_id` field. `assistance_cap`, if given, is a client-requested
    ceiling on `TeachingPlan.assistance_level` (see
    `app.agents.planner.clamp_assistance`) -- it can only lower the help
    given, never raise it.
    """
    raw = await _build_raw_input(text, language, image, topic, assistance_cap)

    result = await run_graph(
        raw,
        llm=llm,
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        retriever=_get_retriever(request),
        knowledge_top_k=_get_knowledge_top_k(request),
        runner=_get_runner(request),
    )
    await session.commit()

    return _to_chat_response(result, conversation_id)


def _get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Return the session factory configured on `app.state`.

    `/chat/stream`'s generator must own its session's full lifetime itself
    (create it, commit it, let it close when the generator exits) rather than
    use the request-scoped `get_session` dependency, whose teardown runs only
    after the streaming response body has finished -- exactly the ordering a
    streaming generator cannot rely on for its own commit. See
    `app.db.session.get_session` / `create_session_factory`.
    """
    factory = getattr(request.app.state, "session_factory", None)
    # `callable` rather than `isinstance(..., async_sessionmaker)`: tests inject
    # a fake factory, so an exact type check would reject them -- but a
    # non-callable here is a real misconfiguration and must fail loudly now,
    # with a clear message, instead of deep inside the streaming generator.
    if factory is None or not callable(factory):
        raise RuntimeError("session_factory is not configured on app.state")
    return cast("async_sessionmaker[AsyncSession]", factory)


def _sse_frame(event: str, data: object) -> str:
    """Format one SSE frame. `data` is JSON-encoded; frame text is otherwise fixed."""
    payload = data if isinstance(data, str) else json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


async def _chat_stream_events(
    request: Request,
    llm: LLMClient,
    current_user: AuthUser,
    raw: RawInput,
    conversation_id: UUID | None,
) -> AsyncIterator[str]:
    """Drive `stream_graph`, yielding SSE frames.

    Exactly one terminal frame (`done` or `error`) is always emitted, even if
    the graph raises. Owns its own session (see `_get_session_factory`):
    created here, committed here (mirroring exactly where `/chat` commits),
    closed on exit.
    """
    # Inside the `try`: a missing/invalid `session_factory` must still produce
    # the terminal `error` frame this docstring promises. Called outside, its
    # `RuntimeError` escaped the generator and the client saw HTTP 200 with an
    # empty body -- `frontend/api_client.py` reads that as success with zero
    # frames, and nothing was logged.
    try:
        session_factory = _get_session_factory(request)
        async with session_factory() as session:
            try:
                final_result: GraphRunResult | None = None
                async for event in stream_graph(
                    raw,
                    llm=llm,
                    session=session,
                    user_id=current_user.id,
                    conversation_id=conversation_id,
                    retriever=_get_retriever(request),
                    knowledge_top_k=_get_knowledge_top_k(request),
                    runner=_get_runner(request),
                ):
                    if isinstance(event, GraphStageEvent):
                        yield _sse_frame("stage", {"node": event.node, "label": event.label})
                    else:
                        final_result = event.result

                if final_result is None:
                    raise RuntimeError("stream_graph completed without a result event")

                await session.commit()
            except Exception:
                await session.rollback()
                raise
            body = _to_chat_response(final_result, conversation_id)
            yield _sse_frame("done", body.model_dump_json())
    except Exception as exc:
        # Exception *type* only, never the traceback or message: this codebase
        # logs failures this way everywhere (`safe_node`, `app.execution.*`,
        # `ping_db`) precisely because an exception's text can carry untrusted
        # user input or secrets.
        logger.warning("chat stream failed: %s", type(exc).__name__)
        yield _sse_frame("error", {"detail": STREAM_ERROR_DETAIL})


@router.post("/chat/stream")
async def chat_stream(
    request: Request,
    llm: Annotated[LLMClient, Depends(get_llm)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    text: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form(max_length=32)] = None,
    image: Annotated[UploadFile | None, File()] = None,
    conversation_id: Annotated[UUID | None, Form()] = None,
    topic: Annotated[str | None, Form(max_length=64)] = None,
    assistance_cap: Annotated[str | None, Form(max_length=16)] = None,
) -> StreamingResponse:
    """Streaming counterpart of `POST /chat`: same request shape, validation,
    and auth; emits SSE `stage` events as the graph progresses, then a single
    terminal `done` frame carrying the exact same body `POST /chat` returns
    (or an `error` frame with a fixed, safe message on failure).
    """
    raw = await _build_raw_input(text, language, image, topic, assistance_cap)

    return StreamingResponse(
        _chat_stream_events(request, llm, current_user, raw, conversation_id),
        media_type="text/event-stream",
    )
