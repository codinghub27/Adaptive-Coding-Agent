"""`/auth` routes: register, login, refresh (rotation + reuse detection), logout.

Every route is public (none depend on `get_current_user`) and owns its own
transaction: on success it commits its own writes, mirroring `POST /chat`
(`app.graph.api`). Every response that carries a token pair sets
`Cache-Control: no-store` and `Pragma: no-cache` (RFC 6749 SS5.1), and every
401 here carries `WWW-Authenticate: Bearer` via `app.auth.deps.unauthorized`.
Tokens and passwords are never logged or echoed back in a response body or
error detail.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_app_settings, unauthorized
from app.auth.security import (
    InvalidTokenError,
    PasswordPolicyError,
    TokenExpiredError,
    decode_token,
    issue_access_token,
    issue_refresh_token,
    verify_password_async,
)
from app.config import Settings
from app.db.auth import (
    HandleTakenError,
    create_refresh_token,
    create_user,
    get_refresh_token,
    get_user_by_handle,
    lock_user,
    revoke_all_for_user,
    revoke_refresh_token_by_id,
    revoke_session,
)
from app.db.session import get_session
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenPair,
)

__all__ = ["router"]

router = APIRouter(prefix="/auth", tags=["auth"])

#: Fixed detail strings -- never interpolate user input or secrets into these.
DETAIL_USERNAME_TAKEN: Final = "username already taken"
DETAIL_LOGIN_FAILED: Final = "incorrect username or password"
DETAIL_REFRESH_EXPIRED: Final = "refresh token expired"
DETAIL_REFRESH_INVALID: Final = "invalid refresh token"
DETAIL_REFRESH_REUSE: Final = "refresh token reuse detected"
DETAIL_REFRESH_ALREADY_USED: Final = "refresh token already used"
DETAIL_UNSUPPORTED_MEDIA_TYPE: Final = "unsupported media type"

_NO_STORE_HEADERS: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}

#: Documents both accepted `POST /auth/login` body shapes in OpenAPI/`/docs`,
#: since neither is declared as a normal FastAPI parameter (see `login`
#: below) -- merged onto the auto-generated operation via `openapi_extra`
#: (`fastapi.utils.deep_dict_update`).
_LOGIN_OPENAPI_EXTRA: Final[dict[str, Any]] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {"schema": LoginRequest.model_json_schema()},
            "application/x-www-form-urlencoded": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "username": {"type": "string", "title": "Username"},
                        "password": {
                            "type": "string",
                            "format": "password",
                            "title": "Password",
                        },
                    },
                    "required": ["username", "password"],
                }
            },
        },
    }
}


def _set_no_store(response: Response) -> None:
    """Mark a response carrying tokens as never cacheable/storable."""
    for key, value in _NO_STORE_HEADERS.items():
        response.headers[key] = value


def _missing_field_error(field: str) -> dict[str, Any]:
    """A pydantic-shaped "field required" error for a form field not sent."""
    return {"type": "missing", "loc": ("body", field), "msg": "Field required"}


async def _parse_login_body(request: Request) -> tuple[str, str]:
    """Extract `(username, password)` from a JSON or form `/auth/login` body.

    - `application/json` -> validated against `LoginRequest` (`username`
      1-64 chars, `password` 1-256 chars).
    - `application/x-www-form-urlencoded` or `multipart/form-data` -> read
      directly from the form, exactly as `OAuth2PasswordRequestForm` (the
      Swagger "Authorize" password flow posts this way) does: only presence
      is required, no length bounds.
    - anything else -> 415.

    A missing/invalid field raises `RequestValidationError` so it goes
    through `app.main.create_app`'s `/auth/*` 422 handler, which strips the
    offending input from the response -- never the password itself.
    """
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()

    if media_type == "application/json":
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            raise RequestValidationError(
                [{"type": "json_invalid", "loc": ("body",), "msg": "Invalid JSON body"}]
            ) from None
        try:
            body = LoginRequest.model_validate(payload)
        except PydanticValidationError as exc:
            # Prefix `loc` with "body", matching FastAPI's own convention for
            # a body-parameter validation error (e.g. `RegisterRequest`'s).
            errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
            raise RequestValidationError(errors) from None
        return body.username, body.password

    if media_type in ("application/x-www-form-urlencoded", "multipart/form-data"):
        form = await request.form()
        username = form.get("username")
        password = form.get("password")
        errors = [
            _missing_field_error(field)
            for field, value in (("username", username), ("password", password))
            if not isinstance(value, str)
        ]
        if errors:
            raise RequestValidationError(errors)
        assert isinstance(username, str)
        assert isinstance(password, str)
        return username, password

    raise HTTPException(status_code=415, detail=DETAIL_UNSUPPORTED_MEDIA_TYPE)


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(
    body: RegisterRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RegisterResponse:
    """Create a new user with a bcrypt-hashed password.

    422 for an invalid username or a password failing the length policy (the
    fixed validation message, never the password itself); 409 if the
    username is already taken.
    """
    try:
        user = await create_user(session, body.username, body.password)
    except HandleTakenError:
        raise HTTPException(status_code=409, detail=DETAIL_USERNAME_TAKEN) from None
    except (ValueError, PasswordPolicyError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    await session.commit()
    return RegisterResponse(id=user.id, username=user.handle)


@router.post("/login", response_model=TokenPair, openapi_extra=_LOGIN_OPENAPI_EXTRA)
async def login(
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TokenPair:
    """Exchange a username + password for a new access/refresh token pair.

    Accepts either a JSON body (`{"username", "password"}`) or a
    form-encoded body (`application/x-www-form-urlencoded` or
    `multipart/form-data`, the shape `OAuth2PasswordRequestForm` and the
    Swagger "Authorize" password flow send) -- see `_parse_login_body`.
    `verify_password_async` always runs a bcrypt comparison (against a dummy
    hash when the user or its password hash is missing) in a worker thread,
    so an unknown username and a wrong password take the same time and get
    the identical 401 detail, without blocking the event loop.
    """
    username, password = await _parse_login_body(request)

    user = await get_user_by_handle(session, username)
    password_hash = user.password_hash if user is not None else None
    if not await verify_password_async(password, password_hash) or user is None:
        raise unauthorized(DETAIL_LOGIN_FAILED)

    session_id = uuid4()
    access = issue_access_token(user_id=user.id, session_id=session_id, settings=settings)
    refresh = issue_refresh_token(user_id=user.id, session_id=session_id, settings=settings)
    await create_refresh_token(
        session,
        token_id=refresh.jti,
        user_id=user.id,
        session_id=session_id,
        token=refresh.token,
        expires_at=refresh.expires_at,
    )
    await session.commit()

    _set_no_store(response)
    return TokenPair(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    response: Response,
    body: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> TokenPair:
    """Rotate a refresh token: revoke the presented one, issue a new pair.

    The new pair keeps the same `sid` (login session). Presenting a token
    already revoked with `revoked_reason == "rotated"` is treated as reuse
    (e.g. a stolen, previously-rotated token) and revokes every session for
    that user -- unless it's within `settings.refresh_reuse_grace_seconds` of
    the rotation, in which case it's treated as a benign duplicate (a retried
    request or a duplicate tab) and just rejected, with no side effects. A
    token revoked for any other reason (e.g. `logout`) is always just
    invalid -- a plain 401 with no side effects.

    Takes a lock on the token's user (`lock_user`) *before* reading the
    refresh-token row: under READ COMMITTED, a plain read here could miss a
    concurrent rotation or revocation for this same user that committed just
    before this transaction started. Waiting for the lock guarantees that,
    once acquired, this transaction's subsequent reads see every change a
    concurrent, already-committed transaction made for this user (e.g. a
    concurrent `logout` racing this rotation, or vice versa).
    """
    try:
        claims = decode_token(body.refresh_token, expected_type="refresh", settings=settings)
    except TokenExpiredError:
        raise unauthorized(DETAIL_REFRESH_EXPIRED) from None
    except InvalidTokenError:
        raise unauthorized(DETAIL_REFRESH_INVALID) from None

    user = await lock_user(session, claims.sub)
    if user is None:
        raise unauthorized(DETAIL_REFRESH_INVALID)

    row = await get_refresh_token(session, body.refresh_token, for_update=True)
    if row is None:
        raise unauthorized(DETAIL_REFRESH_INVALID)

    if row.user_id != claims.sub or row.session_id != claims.sid or row.id != claims.jti:
        raise unauthorized(DETAIL_REFRESH_INVALID)

    if row.revoked:
        now = datetime.now(UTC)
        grace_cutoff = now - timedelta(seconds=settings.refresh_reuse_grace_seconds)
        within_grace = row.revoked_at is not None and row.revoked_at > grace_cutoff
        if row.revoked_reason == "rotated" and within_grace:
            # A duplicate presentation of the just-rotated token (a retried
            # request or a duplicate tab), not theft: reject, but don't
            # punish every other session for it.
            raise unauthorized(DETAIL_REFRESH_ALREADY_USED)
        if row.revoked_reason == "rotated":
            # Reuse of an already-rotated token, outside the grace window:
            # assume it leaked and kill every session for this user. Commit
            # before raising -- `get_session` rolls back on exception, which
            # would otherwise discard the revocation.
            await revoke_all_for_user(session, row.user_id)
            await session.commit()
            raise unauthorized(DETAIL_REFRESH_REUSE)
        # Revoked by logout, or by an earlier reuse revocation: simply
        # invalid, no side effects.
        raise unauthorized(DETAIL_REFRESH_INVALID)

    # Defence in depth: `get_valid_refresh_token`-style expiry filtering isn't
    # used here (this lookup needs revoked rows too, for reuse detection
    # above), so expiry is checked explicitly instead.
    if row.expires_at <= datetime.now(UTC):
        raise unauthorized(DETAIL_REFRESH_EXPIRED)

    await revoke_refresh_token_by_id(session, row.id, reason="rotated")
    new_access = issue_access_token(user_id=user.id, session_id=row.session_id, settings=settings)
    new_refresh = issue_refresh_token(user_id=user.id, session_id=row.session_id, settings=settings)
    await create_refresh_token(
        session,
        token_id=new_refresh.jti,
        user_id=user.id,
        session_id=row.session_id,
        token=new_refresh.token,
        expires_at=new_refresh.expires_at,
    )
    await session.commit()

    _set_no_store(response)
    return TokenPair(
        access_token=new_access.token,
        refresh_token=new_refresh.token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=204)
async def logout(
    body: LogoutRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Revoke the login session the presented refresh token belongs to.

    Always returns 204, whether the token is unknown, already revoked, or
    valid -- idempotent, and reveals nothing about whether the token exists.
    Looked up by hash only (no decode/expiry check needed): only a
    genuinely issued token could ever have a matching row.

    Takes a lock on the token's user (`lock_user`) between finding the row
    and revoking its session: `revoke_session` then runs as a fresh statement
    after the lock wait, so it's guaranteed to see any refresh-token row a
    concurrent, already-committed rotation just inserted for this same
    session (rather than racing it under READ COMMITTED).
    """
    row = await get_refresh_token(session, body.refresh_token)
    if row is not None:
        await lock_user(session, row.user_id)
        await revoke_session(session, user_id=row.user_id, session_id=row.session_id)
        await session.commit()
    return Response(status_code=204)
