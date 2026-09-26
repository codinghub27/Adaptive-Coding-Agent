"""Auth API boundary schemas.

`AuthUser` is what `get_current_user` (`app.auth.deps`) returns to route
handlers -- routes must never receive or touch the `User` ORM object
directly. The remaining schemas are the request/response bodies for the
`/auth/*` routes added in a later packet; they are defined here now so their
shape is settled before those routes are written.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import APIModel

__all__ = [
    "AuthUser",
    "LoginRequest",
    "LogoutRequest",
    "MeResponse",
    "RefreshRequest",
    "RegisterRequest",
    "RegisterResponse",
    "TokenPair",
]


class AuthUser(APIModel):
    """The authenticated user for the current request, as routes see it."""

    id: UUID
    handle: str
    session_id: UUID


class RegisterRequest(APIModel):
    """Request body for `POST /auth/register`.

    `username` is the public name for what the DB/internal layers still call
    a "handle" (`app.db.models.user.User.handle`, `app.db.auth.create_user`);
    the rename is API-surface only. The password length policy itself lives
    in `app.auth.security` (`validate_password`); this only bounds the field
    size at the API boundary.
    """

    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256, repr=False)


class RegisterResponse(APIModel):
    """Response body for `POST /auth/register`."""

    id: UUID
    username: str


class MeResponse(APIModel):
    """Response body for `GET /auth/me`. Carries no token."""

    id: UUID
    username: str
    created_at: datetime


class LoginRequest(APIModel):
    """Request body for `POST /auth/login` when sent as JSON.

    Form-encoded logins (`application/x-www-form-urlencoded` or
    `multipart/form-data`, including the Swagger "Authorize" OAuth2 password
    flow) are read directly from the form instead of this model -- see
    `app.auth.routes.login`.
    """

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256, repr=False)


class TokenPair(APIModel):
    """An issued access + refresh token pair.

    `access_token` and `refresh_token` use `Field(repr=False)` so neither
    value ever appears in a `repr()`/`str()` of this model (e.g. in logs or
    debugger output).
    """

    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    token_type: Literal["bearer"] = "bearer"
    #: Access-token lifetime in seconds.
    expires_in: int


class RefreshRequest(APIModel):
    """Request body for `POST /auth/refresh`."""

    refresh_token: str = Field(min_length=1, max_length=4096, repr=False)


class LogoutRequest(APIModel):
    """Request body for `POST /auth/logout`."""

    refresh_token: str = Field(min_length=1, max_length=4096, repr=False)
