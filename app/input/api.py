"""`POST /understand` — turn raw text and/or an image into a `StructuredInput`
plus its classified `Intent`.

All request content (text, code, error text, image bytes/pixels) is
**untrusted user data**; it is only ever passed to the deterministic
normalizer or the vision/intent LLM calls, never logged, and never echoed
back inside error details. The route requires a valid bearer access token
(`get_current_user`); identity comes from that token only.
"""

import re
from typing import Annotated, Final

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.deps import get_current_user
from app.input.intent import classify_intent
from app.input.normalize import MAX_TEXT_CHARS, merge_inputs, normalize_text
from app.input.vision import MAX_IMAGE_BYTES, ImageValidationError, extract_from_image
from app.llm.base import LLMClient, LLMError
from app.schemas import StructuredInput, UnderstandResponse
from app.schemas.auth import AuthUser

__all__ = [
    "IMAGE_VALIDATION_DETAIL",
    "IMAGE_VALIDATION_STATUS",
    "MAX_REQUEST_BYTES",
    "BodySizeLimitMiddleware",
    "router",
    "get_llm",
    "valid_language_hint",
    "validate_request",
]

router = APIRouter(tags=["input"])

# Generous headroom over a single image + a single max-length text field:
# covers multipart boundary/header overhead and a text field sent alongside
# an image, without being so tight that legitimate requests get rejected.
MAX_REQUEST_BYTES: Final = MAX_IMAGE_BYTES + 4 * MAX_TEXT_CHARS + 64 * 1024

_LANGUAGE_HINT_RE: Final = re.compile(r"^[A-Za-z0-9+#._-]+$")


class _BodyTooLarge(Exception):
    """Raised by the wrapped `receive` once a streamed body exceeds the limit."""


class BodySizeLimitMiddleware:
    """Pure-ASGI middleware rejecting oversized `POST {path}` request bodies.

    FastAPI/Starlette buffer the *entire* request body (spooling multipart
    uploads to disk) before any dependency or route code runs, so a
    per-field size check inside the handler (e.g. capping how many bytes are
    `read()` from an `UploadFile`) is too late to bound memory/disk use for
    an oversized request -- this has to run in front of routing, as ASGI
    middleware.

    When `Content-Length` is present it is checked up front. For bodies
    without a `Content-Length` (e.g. chunked transfer encoding), bytes are
    counted as they stream through `receive`, aborting with 413 as soon as
    the running total exceeds `max_bytes`.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int, path: str = "/understand") -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared_bytes = int(content_length)
            except ValueError:
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > self.max_bytes:
                await self._reject(scope, receive, send)
                return

        try:
            await self.app(scope, self._counting_receive(receive), send)
        except _BodyTooLarge:
            await self._reject(scope, receive, send)

    def _counting_receive(self, receive: Receive) -> Receive:
        total = 0

        async def wrapped_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise _BodyTooLarge
            return message

        return wrapped_receive

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": "request body too large"}, status_code=413)
        await response(scope, receive, send)


IMAGE_VALIDATION_STATUS: dict[str, int] = {
    "too_large": 413,
    "unsupported_type": 415,
    "empty": 422,
}
IMAGE_VALIDATION_DETAIL: dict[str, str] = {
    "too_large": "image exceeds maximum size",
    "unsupported_type": "unsupported image type",
    "empty": "image is empty",
}


def get_llm(request: Request) -> LLMClient:
    """Return the shared `LLMClient` configured on `app.state`."""
    llm = request.app.state.llm
    if not isinstance(llm, LLMClient):
        raise RuntimeError("llm is not configured on app.state")
    return llm


def validate_request(text: str | None, image: UploadFile | None) -> None:
    if not (text and text.strip()) and image is None:
        raise HTTPException(status_code=422, detail="provide text and/or an image")


def _text_to_structured(text: str, language: str | None) -> StructuredInput:
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(status_code=413, detail="text exceeds maximum length")
    return normalize_text(text, language_hint=language)


async def _image_to_structured(image: UploadFile, llm: LLMClient) -> StructuredInput:
    data = await image.read(MAX_IMAGE_BYTES + 1)
    try:
        return await extract_from_image(llm, data, declared_mime=image.content_type)
    except ImageValidationError as exc:
        raise HTTPException(
            status_code=IMAGE_VALIDATION_STATUS[exc.reason],
            detail=IMAGE_VALIDATION_DETAIL[exc.reason],
        ) from None
    except LLMError:
        raise HTTPException(status_code=502, detail="vision extraction failed") from None


def _combine(
    text_input: StructuredInput | None, image_input: StructuredInput | None
) -> StructuredInput:
    if text_input is not None and image_input is not None:
        return merge_inputs(image_input, text_input)
    if image_input is not None:
        return image_input
    if text_input is not None:
        return text_input
    # Unreachable when called from `understand()`: `validate_request` has
    # already rejected the request (422) if neither text nor an image was
    # supplied, so at least one of the two inputs is always set here.
    raise RuntimeError("_combine called with neither text_input nor image_input")


def valid_language_hint(language: str | None) -> str | None:
    """Return `language` if it looks like a plausible language tag, else `None`.

    An implausible value (e.g. containing spaces or punctuation outside
    `+#._-`) is silently ignored rather than passed through to the
    normalizer -- it only ever seeds the language guess, so a bad value
    should behave like no hint at all, not an error.
    """
    if language is not None and _LANGUAGE_HINT_RE.match(language):
        return language
    return None


@router.post("/understand", response_model=UnderstandResponse)
async def understand(
    llm: Annotated[LLMClient, Depends(get_llm)],
    _current_user: Annotated[AuthUser, Depends(get_current_user)],
    text: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form(max_length=32)] = None,
    image: Annotated[UploadFile | None, File()] = None,
) -> UnderstandResponse:
    """Normalize `text`/`image` into a `StructuredInput` and classify its intent.

    Requires authentication (`get_current_user`); the identity itself is
    unused here -- `/understand` has no user-scoped state -- but the route is
    still gated so it can't be used to probe the LLM/vision pipeline
    unauthenticated.
    """
    validate_request(text, image)

    language_hint = valid_language_hint(language)
    text_input = _text_to_structured(text, language_hint) if text and text.strip() else None
    image_input = await _image_to_structured(image, llm) if image is not None else None

    structured = _combine(text_input, image_input)
    if structured.is_empty:
        raise HTTPException(status_code=422, detail="no content could be extracted")

    intent = await classify_intent(structured, llm)
    return UnderstandResponse(input=structured, intent=intent)
