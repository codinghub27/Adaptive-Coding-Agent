"""Shared pytest fixtures for the Adaptive Coding Agent test suite."""

import socket
from collections.abc import Callable, Generator
from urllib.parse import urlsplit

import pytest

from app.config import Settings, get_database_settings, get_settings

#: Every environment variable `Settings` (or `DatabaseSettings`) can read.
#: Unit tests must never be influenced by whatever happens to be set in the
#: real process environment; integration tests still work because
#: `get_settings()` reads the `.env` file, which `monkeypatch.delenv` does
#: not touch.
SETTINGS_ENV_VARS = (
    "DATABASE_URL",
    "QDRANT_URL",
    "QDRANT_API_KEY",
    "QDRANT_TIMEOUT",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_VISION_MODEL",
    "GROQ_API_KEY",
    "OPENROUTER_API_KEY",
    "LANGSMITH_TRACING",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "CORS_ORIGINS",
    "APP_ENV",
)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Strip all Settings-related env vars and clear cached settings.

    Ensures unit tests are never influenced by variables set in the real
    process environment (e.g. a developer's shell), and that stale cached
    `Settings`/`DatabaseSettings` instances from a previous test don't leak
    across tests.
    """
    get_settings.cache_clear()
    get_database_settings.cache_clear()
    for var in SETTINGS_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield
    get_settings.cache_clear()
    get_database_settings.cache_clear()


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    """Factory for a minimal, valid `Settings` instance for unit tests.

    Never reads the real `.env` file (`_env_file=None`). Provides sane
    defaults for the required fields; pass keyword overrides to customize
    individual fields for a specific test.
    """

    def _make(**overrides: object) -> Settings:
        params: dict[str, object] = {
            "database_url": "postgresql://u:p@127.0.0.1:1/x",
            "qdrant_url": "http://127.0.0.1:1",
            "groq_api_key": "test-key",
            "langsmith_tracing": False,
        }
        params.update(overrides)
        return Settings(_env_file=None, **params)  # pyright: ignore[reportCallIssue]

    return _make


def _tcp_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _infra_reachable() -> tuple[bool, str]:
    """Probe the real Postgres + Qdrant infra referenced by the real settings.

    Returns `(reachable, reason)`. Uses a plain TCP connect rather than the
    async `ping_db`/`ping_qdrant` helpers so it can run synchronously during
    collection, before any event loop exists.
    """
    try:
        settings = get_settings()
    except Exception as exc:
        return False, f"settings unavailable ({type(exc).__name__})"

    db_url = urlsplit(settings.database_url.replace("+asyncpg", ""))
    db_host, db_port = db_url.hostname or "localhost", db_url.port or 5432
    if not _tcp_reachable(db_host, db_port):
        return False, f"postgres unreachable at {db_host}:{db_port}"

    qdrant_url = urlsplit(settings.qdrant_url)
    qdrant_host, qdrant_port = qdrant_url.hostname or "localhost", qdrant_url.port or 6333
    if not _tcp_reachable(qdrant_host, qdrant_port):
        return False, f"qdrant unreachable at {qdrant_host}:{qdrant_port}"

    return True, ""


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip `integration`-marked tests when the real infra isn't reachable."""
    reachable, reason = _infra_reachable()
    if reachable:
        return
    skip_marker = pytest.mark.skip(reason=f"integration infra unreachable: {reason}")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_marker)
