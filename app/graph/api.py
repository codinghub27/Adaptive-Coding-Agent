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

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.db.session import get_session
from app.graph.build import run_graph
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
from app.llm.base import LLMClient
from app.schemas.auth import AuthUser
from app.schemas.base import APIModel
from app.schemas.event import LearningEventCreate
from app.schemas.intent import IntentResult
from app.schemas.plan import TeachingPlan

__all__ = ["ChatResponse", "router"]

router = APIRouter(tags=["chat"])


class ChatResponse(APIModel):
    """Response body for `POST /chat`: this turn's outcome from the teaching graph."""

    response: str
    route: RouteKey
    intent: IntentResult | None
    plan: TeachingPlan | None
    events: list[LearningEventCreate]
    events_persisted: list[UUID]
    errors: list[NodeError]
    llm_calls: int


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


@router.post("/chat", response_model=ChatResponse)
async def chat(
    llm: Annotated[LLMClient, Depends(get_llm)],
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
    text: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form(max_length=32)] = None,
    image: Annotated[UploadFile | None, File()] = None,
    conversation_id: Annotated[UUID | None, Form()] = None,
    topic: Annotated[str | None, Form(max_length=64)] = None,
) -> ChatResponse:
    """Run one turn of the teaching graph over `text`/`image` and return its outcome.

    The acting user is always `current_user.id`, resolved from the bearer
    access token by `get_current_user` -- there is no caller-supplied
    `user_id` field.
    """
    validate_request(text, image)

    if text is not None and len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="text exceeds maximum length")

    image_bytes = await _read_image(image) if image is not None else None

    raw = RawInput(
        text=text,
        language=valid_language_hint(language),
        image=image_bytes,
        image_mime=image.content_type if image is not None else None,
        topic_hint=topic,
    )

    result = await run_graph(
        raw,
        llm=llm,
        session=session,
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    await session.commit()

    state = result.state
    return ChatResponse(
        response=state.response if state.response is not None else SAFE_FALLBACK_RESPONSE,
        route=state.route if state.route is not None else "clarify",
        intent=state.intent,
        plan=state.plan,
        events=state.events,
        events_persisted=state.events_persisted,
        errors=state.errors,
        llm_calls=result.llm_calls,
    )
