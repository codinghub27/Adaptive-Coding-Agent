"""Tests for `app.auth.security` (password hashing/verification, token hashing)."""

import pytest

from app.auth import security
from app.auth.security import (
    PasswordPolicyError,
    hash_password,
    hash_password_async,
    hash_token,
    verify_password,
    verify_password_async,
)


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the minimum bcrypt cost factor so these tests run quickly."""
    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)


def test_hash_and_verify_round_trip() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash) is True


def test_verify_wrong_password_fails() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert verify_password("wrong password", password_hash) is False


def test_hash_is_not_the_password_and_is_bcrypt() -> None:
    password_hash = hash_password("some password")
    assert password_hash != "some password"
    assert password_hash.startswith("$2b$")


def test_password_under_min_length_raises_without_leaking_value() -> None:
    with pytest.raises(PasswordPolicyError) as exc_info:
        hash_password("1234567")  # 7 bytes
    assert "1234567" not in str(exc_info.value)


def test_password_over_max_length_raises_without_leaking_value() -> None:
    password = "a" * 73  # 73 bytes
    with pytest.raises(PasswordPolicyError) as exc_info:
        hash_password(password)
    assert password not in str(exc_info.value)


def test_72_byte_multibyte_password_is_accepted() -> None:
    # "e" with combining acute accent is 1 code point but must be counted in
    # UTF-8 bytes, not code points, to land exactly on the 72-byte boundary.
    password = "é" * 36  # 2 bytes each in UTF-8 => exactly 72 bytes
    assert len(password.encode("utf-8")) == 72

    password_hash = hash_password(password)
    assert verify_password(password, password_hash) is True


def test_verify_with_none_hash_returns_false() -> None:
    assert verify_password("anything", None) is False


def test_verify_with_malformed_hash_returns_false() -> None:
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


def test_hash_token_is_deterministic() -> None:
    assert hash_token("some-refresh-token") == hash_token("some-refresh-token")


def test_hash_token_is_64_hex_characters() -> None:
    digest = hash_token("some-refresh-token")
    assert len(digest) == 64
    assert all(char in "0123456789abcdef" for char in digest)


def test_hash_token_differs_for_different_tokens() -> None:
    assert hash_token("token-one") != hash_token("token-two")


# --------------------------------------------------------------------------
# async wrappers (Fix 4: bcrypt off the event loop)
# --------------------------------------------------------------------------


async def test_hash_password_async_round_trips_with_verify_password() -> None:
    password_hash = await hash_password_async("correct horse battery staple")
    assert verify_password("correct horse battery staple", password_hash) is True


async def test_hash_password_async_raises_password_policy_error() -> None:
    with pytest.raises(PasswordPolicyError):
        await hash_password_async("1234567")  # 7 bytes


async def test_verify_password_async_matches_verify_password() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert await verify_password_async("correct horse battery staple", password_hash) is True
    assert await verify_password_async("wrong password", password_hash) is False
    assert await verify_password_async("anything", None) is False
