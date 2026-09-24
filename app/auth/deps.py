"""FastAPI auth dependencies: bearer-token extraction and `get_current_user`.

`get_current_user` is the single gate protected routes depend on. It never
raises anything but `HTTPException(401, ..., headers={"WWW-Authenticate":
"Bearer"})`, with one of a small set of fixed detail strings -- the token
itself is never included in a response, a log line, or an exception message.
"""

from typing import Annotated, Final

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import InvalidTokenError, TokenExpiredError, decode_token
from app.config import Settings
from app.db.auth import get_user, session_is_active
from app.db.session import get_session
from app.schemas.auth import AuthUser

__all__ = [
    "DETAIL_INVALID_CREDENTIALS",
    "DETAIL_NOT_AUTHENTICATED",
    "DETAIL_SESSION_REVOKED",
    "DETAIL_TOKEN_EXPIRED",
    "get_app_settings",
    "get_current_user",
    "oauth2_scheme",
    "unauthorized",
]

#: `auto_error=False`: a missing/malformed `Authorization` header should reach
#: `get_current_user` as `token=None` rather than raise FastAPI's own 401, so
#: every failure path goes through `unauthorized()` with our fixed strings.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

DETAIL_NOT_AUTHENTICATED: Final = "not authenticated"
DETAIL_TOKEN_EXPIRED: Final = "token expired"
DETAIL_INVALID_CREDENTIALS: Final = "invalid credentials"
DETAIL_SESSION_REVOKED: Final = "session revoked"


def get_app_settings(request: Request) -> Settings:
    """Return the `Settings` configured on `app.state` by the lifespan."""
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("settings is not configured on app.state")
    return settings


def unauthorized(detail: str) -> HTTPException:
    """A 401 `HTTPException` with the `WWW-Authenticate: Bearer` header."""
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> AuthUser:
    """Resolve the bearer access token into the authenticated `AuthUser`.

    `session` is the request-scoped session from `get_session`; FastAPI
    caches dependency results per request, so this is the same `AsyncSession`
    instance the route (and any other dependency in the same request) sees.
    This function only reads (it writes nothing); if a DB call raises, the
    exception propagates and `get_session` rolls the session back.

    Order of checks, each failing with 401 + `WWW-Authenticate: Bearer`:
    1. No token presented -> `DETAIL_NOT_AUTHENTICATED`.
    2. Token doesn't decode as a valid, unexpired access token ->
       `DETAIL_TOKEN_EXPIRED` or `DETAIL_INVALID_CREDENTIALS`.
    3. The token's subject user no longer exists -> `DETAIL_INVALID_CREDENTIALS`.
    4. The token's session has no active (non-revoked, unexpired) refresh
       token -> `DETAIL_SESSION_REVOKED` (decision D: logout or refresh-token
       reuse detection kills outstanding access tokens immediately).

    On success, commits `session` to end its read-only transaction here
    rather than leaving it open ("idle in transaction") for the rest of the
    route handler. With `expire_on_commit=False` (the session factory's
    configuration) this only releases the transaction/connection -- it never
    expires `user`, and `AuthUser` below is a plain Pydantic model anyway, so
    nothing about the returned value is affected. The route's own writes (if
    any) start a fresh transaction on their next use of `session` as normal.
    """
    if token is None:
        raise unauthorized(DETAIL_NOT_AUTHENTICATED)

    try:
        claims = decode_token(token, expected_type="access", settings=settings)
    except TokenExpiredError:
        raise unauthorized(DETAIL_TOKEN_EXPIRED) from None
    except InvalidTokenError:
        raise unauthorized(DETAIL_INVALID_CREDENTIALS) from None

    user = await get_user(session, claims.sub)
    if user is None:
        raise unauthorized(DETAIL_INVALID_CREDENTIALS)

    if not await session_is_active(session, user_id=claims.sub, session_id=claims.sid):
        raise unauthorized(DETAIL_SESSION_REVOKED)

    auth_user = AuthUser(id=user.id, handle=user.handle, session_id=claims.sid)
    await session.commit()
    return auth_user
