"""Shared test helpers for `tests/auth/test_protected_routes.py` and
`tests/auth/test_auth_manual.py`: both build the *full* app (`create_app()`)
with `get_llm`/`get_app_settings`/`get_session` overridden, then drive real
`/auth/register` + `/auth/login` calls to get real, persisted tokens.

Public names (unlike the module-private `_client_for`/`_login`/etc. these
were originally duplicated as in each file) so both modules can import them
directly instead of redefining them. `make_handle` (not `handle`): a test
assigning `handle = make_handle()` would otherwise shadow the imported
function with a local variable of the same name for the rest of that
function's scope (Python resolves `handle` as local for the *whole*
function as soon as it's assigned anywhere in it, which would make the
right-hand-side call raise `UnboundLocalError`).
"""

from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_app_settings
from app.config import Settings
from app.db.session import get_session
from app.input.api import get_llm
from app.main import create_app
from tests.input.fakes import FakeLLMClient

__all__ = ["PASSWORD", "auth_headers", "client_for", "login", "make_handle", "register"]

#: 29 UTF-8 bytes -- comfortably within the 8-72 byte password policy.
PASSWORD = "correct horse battery staple"


@asynccontextmanager
async def client_for(
    settings: Settings, db_session: AsyncSession, fake: FakeLLMClient
) -> AsyncGenerator[httpx.AsyncClient]:
    """A `create_app()` client with `get_llm`/`get_app_settings`/`get_session` overridden."""
    app = create_app(settings)
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_app_settings] = lambda: settings

    async def session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = session_override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def make_handle() -> str:
    """A fresh, valid, collision-free handle."""
    return f"user-{uuid4().hex[:12]}"


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def register(
    client: httpx.AsyncClient, handle: str, password: str = PASSWORD
) -> httpx.Response:
    return await client.post("/auth/register", json={"handle": handle, "password": password})


async def login(client: httpx.AsyncClient, handle: str, password: str = PASSWORD) -> httpx.Response:
    return await client.post("/auth/login", data={"username": handle, "password": password})
