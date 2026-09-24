"""Fix 6 regression: 422s under `/auth/*` never echo secret request-field
values back in the response body, and no auth schema leaks a secret through
`repr()`/`str()`. Every other path keeps FastAPI's default `RequestValidationError`
behaviour unchanged.

A missing-field pydantic error's `input` is the *entire* input mapping the
model was validated against (see `test_missing_field_error_input_is_the_whole_payload`
below) -- so a request missing only `username` would otherwise echo the
`password` back in `errors()[0]["input"]`, and a too-long `refresh_token`
would echo itself back as the offending `input` value. `app.main.create_app`'s
`RequestValidationError` handler strips `input`/`ctx` for `/auth/*` only.
This applies to both `POST /auth/login`'s body shapes: the JSON one raises
`RequestValidationError` from `app.auth.routes._parse_login_body` itself
(there's no `LoginRequest` FastAPI parameter to validate it automatically).
"""

from collections.abc import Callable

import pytest
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.schemas.auth import LogoutRequest, RefreshRequest, RegisterRequest
from tests.auth.helpers import PASSWORD, client_for, make_handle, register
from tests.input.fakes import FakeLLMClient

MakeSettings = Callable[..., Settings]


def test_missing_field_error_input_is_the_whole_payload() -> None:
    """Sanity check for *why* Fix 6 is needed: pydantic's own `errors()` for a
    missing field reports the whole input mapping (not just the missing
    field) as that error's `input` -- so leaving FastAPI's default handler in
    place would echo a sibling secret field back in a 422 body.
    """

    class _Model(BaseModel):
        username: str = Field(min_length=3, max_length=64)
        password: str = Field(min_length=1, max_length=256)

    with pytest.raises(ValidationError) as exc_info:
        _Model(password="supersecretpassword")  # type: ignore[call-arg]

    (error,) = exc_info.value.errors()
    assert error["input"] == {"password": "supersecretpassword"}


@pytest.mark.db
async def test_register_422_for_missing_username_never_echoes_the_password(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    secret_password = "a-very-particular-password-1"
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        response = await client.post("/auth/register", json={"password": secret_password})

    assert response.status_code == 422
    assert secret_password not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}


@pytest.mark.db
async def test_register_old_handle_field_name_returns_422_without_echoing_the_password(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """The old field name `handle` is no longer accepted: `username` is
    missing (`RegisterRequest.username`) and `handle` is an unrecognized
    extra field, so `password` must not leak through either error's `input`.
    """
    settings = make_settings()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        response = await client.post(
            "/auth/register", json={"handle": make_handle(), "password": PASSWORD}
        )

    assert response.status_code == 422
    assert PASSWORD not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}


@pytest.mark.db
async def test_json_login_missing_password_never_echoes_the_username(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """A JSON `/auth/login` body missing `password` goes through the same
    `/auth/*` redaction handler as `/auth/register` (Fix 6): the 422 body is
    reduced to `loc`/`msg`/`type`, so pydantic's own `input` (the whole
    payload -- see `test_missing_field_error_input_is_the_whole_payload`)
    never leaks a sibling field back.
    """
    settings = make_settings()
    handle = make_handle()
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        await register(client, handle)
        response = await client.post("/auth/login", json={"username": handle})

    assert response.status_code == 422
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}


@pytest.mark.db
async def test_refresh_422_for_overlong_token_never_echoes_it(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    overlong_token = "x" * 4097  # one past RefreshRequest's max_length
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        response = await client.post("/auth/refresh", json={"refresh_token": overlong_token})

    assert response.status_code == 422
    assert overlong_token not in response.text
    for error in response.json()["detail"]:
        assert set(error) == {"loc", "msg", "type"}


@pytest.mark.db
async def test_logout_422_for_overlong_token_never_echoes_it(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    settings = make_settings()
    overlong_token = "y" * 4097
    async with client_for(settings, db_session, FakeLLMClient()) as client:
        response = await client.post("/auth/logout", json={"refresh_token": overlong_token})

    assert response.status_code == 422
    assert overlong_token not in response.text


@pytest.mark.db
async def test_chat_422_keeps_the_default_handler_behaviour(
    make_settings: MakeSettings, db_session: AsyncSession
) -> None:
    """A non-`/auth/*` 422 (here, `/chat` with an over-long `topic` form
    field) is untouched: FastAPI's default handler still runs, `input` still
    present.
    """
    settings = make_settings()
    handle = make_handle()
    fake = FakeLLMClient(chat_content="unused")
    async with client_for(settings, db_session, fake) as client:
        await register(client, handle)
        login_resp = await client.post(
            "/auth/login", data={"username": handle, "password": PASSWORD}
        )
        token = login_resp.json()["access_token"]

        overlong_topic = "t" * 65  # one past `topic`'s Form(max_length=64)
        response = await client.post(
            "/chat",
            data={"text": "why does this fail?", "topic": overlong_topic},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert "input" in error


def test_register_request_repr_and_str_do_not_reveal_the_password() -> None:
    body = RegisterRequest(username="alice-handle", password="super-secret-password")  # noqa: S106
    assert "super-secret-password" not in repr(body)
    assert "super-secret-password" not in str(body)


def test_refresh_request_repr_and_str_do_not_reveal_the_token() -> None:
    body = RefreshRequest(refresh_token="super-secret-refresh-token")  # noqa: S106
    assert "super-secret-refresh-token" not in repr(body)
    assert "super-secret-refresh-token" not in str(body)


def test_logout_request_repr_and_str_do_not_reveal_the_token() -> None:
    body = LogoutRequest(refresh_token="super-secret-refresh-token")  # noqa: S106
    assert "super-secret-refresh-token" not in repr(body)
    assert "super-secret-refresh-token" not in str(body)
