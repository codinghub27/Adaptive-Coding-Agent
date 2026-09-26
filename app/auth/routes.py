"""`/auth` routes: register, login, refresh (rotation + reuse detection),
logout, and `GET /auth/me`.

Every route but `GET /auth/me` is public (none depend on `get_current_user`);
`GET /auth/me` is the one protected route on this router. Every route owns
its own transaction: on success it commits its own writes, mirroring
`POST /chat` (`app.graph.api`). Every response that carries a token pair sets
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

from app.auth.deps import (
    DETAIL_INVALID_CREDENTIALS,
    get_app_settings,
    get_current_user,
    unauthorized,
)
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
    get_user,
    get_user_by_handle,
    lock_user,
    revoke_all_for_user,
    revoke_refresh_token_by_id,
    revoke_session,
)
from app.db.session import get_session
from app.schemas.auth import (
    AuthUser,
    LoginRequest,
    LogoutRequest,
    MeResponse,
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

#: Name and path of the httpOnly refresh-token cookie set by `login`/
#: `refresh` and cleared by `logout` (D8 in `docs/features/UI-integration.md`).
#: Scoped to `/auth` so the browser only ever attaches it to `/auth/*`
#: requests, never to `/chat` or any other route.
REFRESH_COOKIE_NAME: Final = "refresh_token"
REFRESH_COOKIE_PATH: Final = "/auth"

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

#: Documents `POST /auth/refresh`'s/`POST /auth/logout`'s body: it's now
#: optional (the httpOnly cookie is the fallback -- see
#: `_resolve_refresh_token`), which a plain FastAPI parameter can't express
#: as cleanly, so this is merged onto the operation via `openapi_extra`
#: the same way `_LOGIN_OPENAPI_EXTRA` documents `/auth/login`.
_REFRESH_OPENAPI_EXTRA: Final[dict[str, Any]] = {
    "requestBody": {
        "required": False,
        "content": {"application/json": {"schema": RefreshRequest.model_json_schema()}},
    }
}
_LOGOUT_OPENAPI_EXTRA: Final[dict[str, Any]] = {
    "requestBody": {
        "required": False,
        "content": {"application/json": {"schema": LogoutRequest.model_json_schema()}},
    }
}


def _set_no_store(response: Response) -> None:
    """Mark a response carrying tokens as never cacheable/storable."""
    for key, value in _NO_STORE_HEADERS.items():
        response.headers[key] = value


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """Set the httpOnly refresh-token cookie on a login/refresh response.

    `SameSite=Strict` + `Path=/auth`: the browser only ever attaches this
    cookie to a same-site `/auth/*` request, never cross-site and never to
    `/chat` or any other route. `secure=settings.refresh_cookie_secure` is
    only false for local `http://localhost` development -- see
    `Settings.refresh_cookie_secure`.
    """
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.refresh_token_expire_days * 86400,
        path=REFRESH_COOKIE_PATH,
        secure=settings.refresh_cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    """Clear the refresh-token cookie on logout.

    The name/path/samesite/httponly attributes must match exactly what
    `_set_refresh_cookie` sent -- a browser only drops a cookie when a
    `Set-Cookie` deletion matches those attributes.
    """
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        samesite="strict",
    )


def _missing_field_error(field: str) -> dict[str, Any]:
    """A pydantic-shaped "field required" error for a form field not sent."""
    return {"type": "missing", "loc": ("body", field), "msg": "Field required"}


async def _resolve_refresh_token(request: Request) -> str:
    """Extract the refresh token for `/auth/refresh` and `/auth/logout`.

    The token may come from either a JSON body (`{"refresh_token": ...}`) or
    the httpOnly cookie (`REFRESH_COOKIE_NAME`) `login`/`refresh` set -- the
    body wins when both are present. Mirrors `_parse_login_body`'s approach:
    a missing/invalid body value raises `RequestValidationError` so it goes
    through the `/auth/*` 422 handler (never echoing the token), and neither
    a body nor a cookie present raises the identical "field required" 422 a
    missing body produced before this cookie fallback existed.
    """
    body_bytes = await request.body()
    if body_bytes:
        try:
            payload = json.loads(body_bytes)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and "refresh_token" in payload:
            try:
                validated = RefreshRequest.model_validate(payload)
            except PydanticValidationError as exc:
                errors = [{**error, "loc": ("body", *error["loc"])} for error in exc.errors()]
                raise RequestValidationError(errors) from None
            return validated.refresh_token

    cookie_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie_token:
        return cookie_token

    raise RequestValidationError([_missing_field_error("refresh_token")])


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
    _set_refresh_cookie(response, refresh.token, settings)
    return TokenPair(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/refresh", response_model=TokenPair, openapi_extra=_REFRESH_OPENAPI_EXTRA)
async def refresh(
    request: Request,
    response: Response,
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

    The refresh token comes from either the JSON body or the httpOnly
    cookie `login`/`refresh` set (`_resolve_refresh_token`); the body wins
    when both are present, so the existing body-only contract keeps working
    verbatim.
    """
    token = await _resolve_refresh_token(request)
    try:
        claims = decode_token(token, expected_type="refresh", settings=settings)
    except TokenExpiredError:
        raise unauthorized(DETAIL_REFRESH_EXPIRED) from None
    except InvalidTokenError:
        raise unauthorized(DETAIL_REFRESH_INVALID) from None

    user = await lock_user(session, claims.sub)
    if user is None:
        raise unauthorized(DETAIL_REFRESH_INVALID)

    row = await get_refresh_token(session, token, for_update=True)
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
    _set_refresh_cookie(response, new_refresh.token, settings)
    return TokenPair(
        access_token=new_access.token,
        refresh_token=new_refresh.token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", status_code=204, openapi_extra=_LOGOUT_OPENAPI_EXTRA)
async def logout(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Revoke the login session the presented refresh token belongs to.

    Always returns 204, whether the token is unknown, already revoked, or
    valid -- idempotent, and reveals nothing about whether the token exists.
    Looked up by hash only (no decode/expiry check needed): only a
    genuinely issued token could ever have a matching row. The refresh
    token comes from either the JSON body or the httpOnly cookie
    (`_resolve_refresh_token`); the body wins when both are present. The
    cookie is cleared on every path -- unknown token, already revoked, or
    valid -- so logout stays idempotent and always 204.

    Takes a lock on the token's user (`lock_user`) between finding the row
    and revoking its session: `revoke_session` then runs as a fresh statement
    after the lock wait, so it's guaranteed to see any refresh-token row a
    concurrent, already-committed rotation just inserted for this same
    session (rather than racing it under READ COMMITTED).
    """
    token = await _resolve_refresh_token(request)
    row = await get_refresh_token(session, token)
    if row is not None:
        await lock_user(session, row.user_id)
        await revoke_session(session, user_id=row.user_id, session_id=row.session_id)
        await session.commit()

    response = Response(status_code=204)
    _clear_refresh_cookie(response)
    return response


@router.get("/me", response_model=MeResponse)
async def read_me(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> MeResponse:
    """Return the authenticated user's own identity.

    `AuthUser` (from `get_current_user`) doesn't carry `created_at`, so the
    user row is looked up again by id. 401 with `DETAIL_INVALID_CREDENTIALS`
    if the user has been deleted since the access token was issued (mirrors
    `get_current_user`'s own check). Carries no token, so there is no need
    for the `Cache-Control: no-store` header the token-issuing routes set.
    """
    user = await get_user(session, current_user.id)
    if user is None:
        raise unauthorized(DETAIL_INVALID_CREDENTIALS)
    return MeResponse(id=user.id, username=user.handle, created_at=user.created_at)
