"""Password hashing and verification, and access/refresh JWT issue/decode.

Passwords are hashed with bcrypt (slow, salted).

## Token claims

Both access and refresh tokens carry the same claim set:

- `sub` -- the user id (`UUID`).
- `sid` -- the login session id (`UUID`), stable across refresh-token
  rotation (decision D in `docs/features/AUTH-jwt.md`). Lets
  `get_current_user` revoke every access token tied to a session with one
  indexed query, without an access-token denylist.
- `jti` -- a fresh, random token id (`UUID`), unique per issued token.
- `type` -- `"access"` or `"refresh"`.
- `iat` / `exp` -- issued-at / expiry, as UTC epoch seconds.

### Why the `type` claim exists

Access and refresh tokens are both just HS256 JWTs signed with the same
secret, so nothing about their *encoding* distinguishes them. Without a
`type` claim, a stolen (or merely leaked-to-a-less-trusted-context) refresh
token could be presented directly to a route expecting an access token --
and vice versa, an access token (deliberately short-lived and never
persisted) could be replayed against `/auth/refresh` to mint new long-lived
refresh tokens. `decode_token` requires the caller to state which `type` it
expects and rejects any mismatch as `InvalidTokenError`, so a token is only
ever valid for the one purpose it was issued for.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal
from uuid import UUID, uuid4

import anyio.to_thread
import bcrypt
import jwt
from jwt import InvalidTokenError as _PyJWTInvalidTokenError
from pydantic import ValidationError

from app.auth.timeutil import resolve_now
from app.config import Settings
from app.schemas.base import APIModel

__all__ = [
    "BCRYPT_ROUNDS",
    "MAX_PASSWORD_BYTES",
    "MIN_PASSWORD_BYTES",
    "InvalidTokenError",
    "IssuedToken",
    "PasswordPolicyError",
    "TokenClaims",
    "TokenError",
    "TokenExpiredError",
    "TokenType",
    "decode_token",
    "hash_password",
    "hash_password_async",
    "hash_token",
    "issue_access_token",
    "issue_refresh_token",
    "validate_password",
    "verify_password",
    "verify_password_async",
]

TokenType = Literal["access", "refresh"]

_REQUIRED_CLAIMS: Final = ["exp", "iat", "sub", "sid", "jti", "type"]

MIN_PASSWORD_BYTES: Final = 8
#: bcrypt silently ignores bytes beyond 72; we reject rather than truncate so
#: a caller never believes a longer password was actually used in full.
MAX_PASSWORD_BYTES: Final = 72
#: Read at call time (not captured), so tests can `monkeypatch.setattr` this
#: module's `BCRYPT_ROUNDS` to a lower value for speed.
BCRYPT_ROUNDS: Final = 12

#: A valid bcrypt hash of a fixed, never-used password. `verify_password`
#: checks against this when no real hash exists, so a login attempt against
#: an unknown handle takes about as long as one against a known handle with
#: the wrong password -- response timing doesn't reveal whether a handle
#: exists.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing-only", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


class PasswordPolicyError(ValueError):
    """A password fails the length policy. Messages never include the password."""


def validate_password(password: str) -> None:
    """Raise `PasswordPolicyError` unless `password` is 8-72 UTF-8 bytes."""
    length = len(password.encode("utf-8"))
    if length < MIN_PASSWORD_BYTES or length > MAX_PASSWORD_BYTES:
        raise PasswordPolicyError(
            f"password must be between {MIN_PASSWORD_BYTES} and "
            f"{MAX_PASSWORD_BYTES} bytes when UTF-8 encoded"
        )


def hash_password(password: str) -> str:
    """Validate and bcrypt-hash a password.

    Raises `PasswordPolicyError` if the password fails `validate_password`.
    """
    validate_password(password)
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


async def hash_password_async(password: str) -> str:
    """`hash_password`, run in a worker thread so bcrypt never blocks the event loop.

    Raises `PasswordPolicyError` (re-raised from the worker thread) if the
    password fails `validate_password`.
    """
    return await anyio.to_thread.run_sync(hash_password, password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check `password` against `password_hash`; never raises.

    Returns `False` for a missing (`None`) hash, a malformed hash, or a
    password over `MAX_PASSWORD_BYTES`. When `password_hash` is `None`, still
    runs a bcrypt comparison against a dummy hash so response timing doesn't
    reveal whether the user exists.
    """
    target = password_hash.encode("utf-8") if password_hash is not None else _DUMMY_HASH
    try:
        matched = bcrypt.checkpw(password.encode("utf-8"), target)
    except ValueError:
        return False
    return matched and password_hash is not None


async def verify_password_async(password: str, password_hash: str | None) -> bool:
    """`verify_password`, run in a worker thread so bcrypt never blocks the event loop."""
    return await anyio.to_thread.run_sync(verify_password, password, password_hash)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of `token`, for persisting refresh tokens.

    Unlike passwords, refresh tokens are high-entropy, machine-generated JWTs
    rather than something a human might reuse or brute-force -- so a fast,
    deterministic hash is safe here (no salt needed) and, unlike bcrypt,
    supports looking the token up by its hash with an indexed equality query.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenError(Exception):
    """Base class for token issue/decode failures.

    Messages are always fixed strings -- never the token or the secret --
    since these errors may end up in logs or (via FastAPI) in a response.
    """


class TokenExpiredError(TokenError):
    """The token's signature and shape are valid, but it has expired."""

    def __init__(self) -> None:
        super().__init__("token has expired")


class InvalidTokenError(TokenError):
    """The token is malformed, mis-signed, the wrong type, or otherwise unusable.

    Named `InvalidTokenError` for a clear call-site error type; PyJWT's own
    `jwt.InvalidTokenError` is imported under the alias `_PyJWTInvalidTokenError`
    in this module so the two are never confused with each other.
    """

    def __init__(self) -> None:
        super().__init__("token is invalid")


class TokenClaims(APIModel):
    """The validated claim set of a decoded access or refresh token."""

    sub: UUID
    sid: UUID
    jti: UUID
    type: TokenType
    iat: datetime
    exp: datetime


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly issued JWT, along with the metadata needed to persist it."""

    token: str
    jti: UUID
    expires_at: datetime


def _issue_token(
    *,
    user_id: UUID,
    session_id: UUID,
    token_type: TokenType,
    lifetime: timedelta,
    settings: Settings,
    now: datetime | None,
) -> IssuedToken:
    issued_at = resolve_now(now)
    expires_at = issued_at + lifetime
    jti = uuid4()
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(jti),
        "type": token_type,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return IssuedToken(token=token, jti=jti, expires_at=expires_at)


def issue_access_token(
    *, user_id: UUID, session_id: UUID, settings: Settings, now: datetime | None = None
) -> IssuedToken:
    """Issue a short-lived access token (`settings.access_token_expire_minutes`)."""
    return _issue_token(
        user_id=user_id,
        session_id=session_id,
        token_type="access",
        lifetime=timedelta(minutes=settings.access_token_expire_minutes),
        settings=settings,
        now=now,
    )


def issue_refresh_token(
    *, user_id: UUID, session_id: UUID, settings: Settings, now: datetime | None = None
) -> IssuedToken:
    """Issue a long-lived refresh token (`settings.refresh_token_expire_days`)."""
    return _issue_token(
        user_id=user_id,
        session_id=session_id,
        token_type="refresh",
        lifetime=timedelta(days=settings.refresh_token_expire_days),
        settings=settings,
        now=now,
    )


def decode_token(token: str, *, expected_type: TokenType, settings: Settings) -> TokenClaims:
    """Verify and decode `token`, returning its claims.

    Raises `TokenExpiredError` if the signature and shape are valid but the
    token has expired, or `InvalidTokenError` for any other failure: a bad
    signature, an unsupported/mismatched algorithm, a missing required claim,
    a claim that fails validation, or `type != expected_type`. Never raises
    a PyJWT or Pydantic exception directly.

    Allows `settings.jwt_leeway_seconds` of clock skew on `iat`/`exp`
    validation, so a token issued or verified a few seconds off from another
    clock (e.g. this process vs. the DB, or two app instances) isn't
    spuriously rejected.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": _REQUIRED_CLAIMS},
            leeway=settings.jwt_leeway_seconds,
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpiredError() from exc
    except _PyJWTInvalidTokenError as exc:
        raise InvalidTokenError() from exc

    try:
        claims = TokenClaims.model_validate(payload)
    except ValidationError as exc:
        raise InvalidTokenError() from exc

    if claims.type != expected_type:
        raise InvalidTokenError()

    return claims
