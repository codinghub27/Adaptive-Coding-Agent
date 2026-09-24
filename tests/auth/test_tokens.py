"""Tests for `app.auth.security` access/refresh JWT issue and decode.

No DB access -- everything here is pure token issue/decode against a
`Settings` instance built by the `make_settings` factory fixture.
"""

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt as pyjwt
import pytest

from app.auth.security import (
    InvalidTokenError,
    TokenExpiredError,
    decode_token,
    issue_access_token,
    issue_refresh_token,
)
from app.config import Settings

_TOLERANCE = timedelta(seconds=5)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _build_token(
    header: dict[str, Any], payload: dict[str, Any], *, secret: bytes | None, alg: str | None
) -> str:
    """Hand-build a JWT from a header/payload, bypassing PyJWT's own encoder.

    Used to construct tokens PyJWT's `encode` would refuse to produce (e.g.
    `alg: none`, or a payload missing a required claim).
    """
    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    if alg is None or alg == "none":
        signature_b64 = ""
    else:
        assert secret is not None
        digestmod = hashlib.sha256 if alg == "HS256" else hashlib.sha512
        signature = hmac.new(secret, signing_input, digestmod).digest()
        signature_b64 = _b64url(signature)
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def _valid_payload(settings: Settings, *, token_type: str = "access") -> dict[str, Any]:
    now = int(datetime.now(UTC).timestamp())
    return {
        "sub": str(uuid4()),
        "sid": str(uuid4()),
        "jti": str(uuid4()),
        "type": token_type,
        "iat": now,
        "exp": now + 3600,
    }


def test_access_token_round_trips(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    user_id, session_id = uuid4(), uuid4()

    issued = issue_access_token(user_id=user_id, session_id=session_id, settings=settings)
    claims = decode_token(issued.token, expected_type="access", settings=settings)

    assert claims.sub == user_id
    assert claims.sid == session_id
    assert claims.jti == issued.jti
    assert claims.type == "access"
    assert claims.exp - claims.iat == timedelta(minutes=45)
    assert abs(issued.expires_at - (datetime.now(UTC) + timedelta(minutes=45))) < _TOLERANCE


def test_refresh_token_round_trips(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    user_id, session_id = uuid4(), uuid4()

    issued = issue_refresh_token(user_id=user_id, session_id=session_id, settings=settings)
    claims = decode_token(issued.token, expected_type="refresh", settings=settings)

    assert claims.sub == user_id
    assert claims.sid == session_id
    assert claims.type == "refresh"
    assert claims.exp - claims.iat == timedelta(days=7)


def test_custom_expiry_settings_are_honoured(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(access_token_expire_minutes=10, refresh_token_expire_days=2)
    user_id, session_id = uuid4(), uuid4()

    access = issue_access_token(user_id=user_id, session_id=session_id, settings=settings)
    refresh = issue_refresh_token(user_id=user_id, session_id=session_id, settings=settings)

    access_claims = decode_token(access.token, expected_type="access", settings=settings)
    refresh_claims = decode_token(refresh.token, expected_type="refresh", settings=settings)

    assert access_claims.exp - access_claims.iat == timedelta(minutes=10)
    assert refresh_claims.exp - refresh_claims.iat == timedelta(days=2)


def test_expired_access_token_raises_token_expired_error(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    issued_at = datetime.now(UTC) - timedelta(minutes=46)

    issued = issue_access_token(
        user_id=uuid4(), session_id=uuid4(), settings=settings, now=issued_at
    )

    with pytest.raises(TokenExpiredError):
        decode_token(issued.token, expected_type="access", settings=settings)


def test_expired_refresh_token_raises_token_expired_error(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    issued_at = datetime.now(UTC) - timedelta(days=8)

    issued = issue_refresh_token(
        user_id=uuid4(), session_id=uuid4(), settings=settings, now=issued_at
    )

    with pytest.raises(TokenExpiredError):
        decode_token(issued.token, expected_type="refresh", settings=settings)


def test_refresh_token_decoded_as_access_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    issued = issue_refresh_token(user_id=uuid4(), session_id=uuid4(), settings=settings)

    with pytest.raises(InvalidTokenError):
        decode_token(issued.token, expected_type="access", settings=settings)


def test_access_token_decoded_as_refresh_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    issued = issue_access_token(user_id=uuid4(), session_id=uuid4(), settings=settings)

    with pytest.raises(InvalidTokenError):
        decode_token(issued.token, expected_type="refresh", settings=settings)


def test_tampered_payload_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    issued = issue_access_token(user_id=uuid4(), session_id=uuid4(), settings=settings)
    header_b64, payload_b64, signature_b64 = issued.token.split(".")

    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["sub"] = str(uuid4())
    tampered_payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    tampered = f"{header_b64}.{tampered_payload_b64}.{signature_b64}"

    with pytest.raises(InvalidTokenError):
        decode_token(tampered, expected_type="access", settings=settings)


def test_tampered_signature_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    issued = issue_access_token(user_id=uuid4(), session_id=uuid4(), settings=settings)
    header_b64, payload_b64, signature_b64 = issued.token.split(".")
    flipped = ("A" if signature_b64[-1] != "A" else "B") + signature_b64[1:]
    tampered = f"{header_b64}.{payload_b64}.{flipped}"

    with pytest.raises(InvalidTokenError):
        decode_token(tampered, expected_type="access", settings=settings)


def test_wrong_secret_is_invalid(make_settings: Callable[..., Settings]) -> None:
    issuing_settings = make_settings(jwt_secret_key="a" * 32 + "-issuer-only-secret-value")
    verifying_settings = make_settings(jwt_secret_key="b" * 32 + "-verifier-only-secret-val")
    issued = issue_access_token(user_id=uuid4(), session_id=uuid4(), settings=issuing_settings)

    with pytest.raises(InvalidTokenError):
        decode_token(issued.token, expected_type="access", settings=verifying_settings)


def test_alg_none_token_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    payload = _valid_payload(settings)
    token = _build_token({"alg": "none", "typ": "JWT"}, payload, secret=None, alg="none")

    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access", settings=settings)


def test_hs512_token_with_correct_secret_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    payload = _valid_payload(settings)
    secret = settings.jwt_secret_key.get_secret_value().encode("utf-8")
    token = _build_token({"alg": "HS512", "typ": "JWT"}, payload, secret=secret, alg="HS512")

    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access", settings=settings)


@pytest.mark.parametrize("missing_claim", ["sub", "sid", "jti", "type", "iat", "exp"])
def test_missing_required_claim_is_invalid(
    make_settings: Callable[..., Settings], missing_claim: str
) -> None:
    settings = make_settings()
    payload = _valid_payload(settings)
    del payload[missing_claim]
    secret = settings.jwt_secret_key.get_secret_value().encode("utf-8")
    token = _build_token({"alg": "HS256", "typ": "JWT"}, payload, secret=secret, alg="HS256")

    with pytest.raises(InvalidTokenError):
        decode_token(token, expected_type="access", settings=settings)


def test_garbage_string_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(InvalidTokenError):
        decode_token("not-a-jwt-at-all", expected_type="access", settings=settings)


def test_empty_string_is_invalid(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    with pytest.raises(InvalidTokenError):
        decode_token("", expected_type="access", settings=settings)


def test_errors_never_contain_token_or_secret(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    issued_at = datetime.now(UTC) - timedelta(minutes=46)
    expired = issue_access_token(
        user_id=uuid4(), session_id=uuid4(), settings=settings, now=issued_at
    )
    secret = settings.jwt_secret_key.get_secret_value()

    with pytest.raises(TokenExpiredError) as expired_info:
        decode_token(expired.token, expected_type="access", settings=settings)
    assert expired.token not in str(expired_info.value)
    assert secret not in str(expired_info.value)

    with pytest.raises(InvalidTokenError) as invalid_info:
        decode_token("garbage", expected_type="access", settings=settings)
    assert secret not in str(invalid_info.value)


def test_two_issued_tokens_differ_with_different_jti(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    user_id, session_id = uuid4(), uuid4()

    first = issue_access_token(user_id=user_id, session_id=session_id, settings=settings)
    second = issue_access_token(user_id=user_id, session_id=session_id, settings=settings)

    assert first.token != second.token
    assert first.jti != second.jti


def test_naive_now_raises_value_error(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    naive_now = datetime.now()  # noqa: DTZ005 -- intentionally naive for this test

    with pytest.raises(ValueError, match="timezone-aware"):
        issue_access_token(user_id=uuid4(), session_id=uuid4(), settings=settings, now=naive_now)


# --------------------------------------------------------------------------
# clock-skew leeway (Fix 8)
# --------------------------------------------------------------------------


def test_iat_in_near_future_decodes_with_default_leeway_but_not_with_zero(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()  # default jwt_leeway_seconds=10
    issued_at = datetime.now(UTC) + timedelta(seconds=5)
    issued = issue_access_token(
        user_id=uuid4(), session_id=uuid4(), settings=settings, now=issued_at
    )

    decode_token(issued.token, expected_type="access", settings=settings)  # no raise

    zero_leeway_settings = make_settings(jwt_leeway_seconds=0)
    with pytest.raises(InvalidTokenError):
        decode_token(issued.token, expected_type="access", settings=zero_leeway_settings)


def test_token_expired_within_leeway_still_decodes(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()  # default jwt_leeway_seconds=10
    issued_at = datetime.now(UTC) - timedelta(minutes=45, seconds=5)
    issued = issue_access_token(
        user_id=uuid4(), session_id=uuid4(), settings=settings, now=issued_at
    )

    decode_token(issued.token, expected_type="access", settings=settings)  # no raise


def test_token_expired_beyond_leeway_raises_token_expired_error(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()  # default jwt_leeway_seconds=10
    issued_at = datetime.now(UTC) - timedelta(minutes=45, seconds=60)
    issued = issue_access_token(
        user_id=uuid4(), session_id=uuid4(), settings=settings, now=issued_at
    )

    with pytest.raises(TokenExpiredError):
        decode_token(issued.token, expected_type="access", settings=settings)


def test_pyjwt_encode_sanity_reference(make_settings: Callable[..., Settings]) -> None:
    """Sanity check that our hand-built tokens match PyJWT's own encoding shape."""
    settings = make_settings()
    payload = _valid_payload(settings)
    secret = settings.jwt_secret_key.get_secret_value()
    reference = pyjwt.encode(payload, secret, algorithm="HS256")
    ours = _build_token(
        {"alg": "HS256", "typ": "JWT"}, payload, secret=secret.encode("utf-8"), alg="HS256"
    )
    assert reference == ours
