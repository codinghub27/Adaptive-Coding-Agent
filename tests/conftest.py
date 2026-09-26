"""Shared pytest fixtures for the Adaptive Coding Agent test suite."""

import os
import socket
from collections.abc import Callable, Generator
from urllib.parse import urlsplit

import pytest

from app.config import Settings, get_database_settings, get_settings

#: A fixed, test-only JWT secret (64 hex-ish characters, well over the
#: 32-character minimum). Set as a default *before* anything imports
#: `app.main` (which builds `app = create_app()` at import time via
#: `get_settings()`), so the test suite never depends on the developer's
#: `.env` having a real `JWT_SECRET_KEY` set. Never used outside tests.
TEST_JWT_SECRET_KEY = "0123456789abcdef" * 4

os.environ.setdefault("JWT_SECRET_KEY", TEST_JWT_SECRET_KEY)

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
    "KNOWLEDGE_COLLECTION",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "RERANKER_MODEL",
    "FASTEMBED_CACHE_DIR",
    "KNOWLEDGE_TOP_K",
    "KNOWLEDGE_TIMEOUT_S",
    "KNOWLEDGE_STARTUP_TIMEOUT_S",
    "KNOWLEDGE_ENABLED",
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
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "ACCESS_TOKEN_EXPIRE_MINUTES",
    "REFRESH_TOKEN_EXPIRE_DAYS",
    "JWT_LEEWAY_SECONDS",
    "REFRESH_REUSE_GRACE_SECONDS",
    "SANDBOX_ENABLED",
    "SANDBOX_IMAGE",
    "SANDBOX_MEMORY_MB",
    "SANDBOX_MAX_CONCURRENT",
    "SANDBOX_QUEUE_TIMEOUT_S",
    "SANDBOX_STARTUP_TIMEOUT_S",
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
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET_KEY)
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
            # Both providers are keyed so tests are unaffected by which
            # provider `Settings.llm_provider` defaults to.
            "groq_api_key": "test-key",
            "openrouter_api_key": "test-key",
            "langsmith_tracing": False,
            "jwt_secret_key": TEST_JWT_SECRET_KEY,
            # Loading the embedder/reranker models costs real seconds per app
            # instance; tests that specifically exercise knowledge retrieval
            # opt back in with `knowledge_enabled=True`.
            "knowledge_enabled": False,
            # Building a `SandboxRunner` needs a reachable Docker daemon;
            # tests that specifically exercise the sandbox opt back in with
            # `sandbox_enabled=True`.
            "sandbox_enabled": False,
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


def _postgres_reachable() -> tuple[bool, str]:
    """Probe the real Postgres infra referenced by the real settings.

    Returns `(reachable, reason)`. Uses a plain TCP connect rather than the
    async `ping_db` helper so it can run synchronously during collection,
    before any event loop exists.
    """
    try:
        settings = get_settings()
    except Exception as exc:
        return False, f"settings unavailable ({type(exc).__name__})"

    db_url = urlsplit(settings.database_url.replace("+asyncpg", ""))
    db_host, db_port = db_url.hostname or "localhost", db_url.port or 5432
    if not _tcp_reachable(db_host, db_port):
        return False, f"postgres unreachable at {db_host}:{db_port}"

    return True, ""


def _infra_reachable(postgres_ok: bool, postgres_reason: str) -> tuple[bool, str]:
    """Combine an already-computed Postgres probe with a fresh Qdrant probe.

    Returns `(reachable, reason)`. Takes the Postgres result instead of
    re-probing so Postgres is only ever TCP-probed once per collection. Uses a
    plain TCP connect rather than the async `ping_qdrant` helper so it can run
    synchronously during collection, before any event loop exists.
    """
    if not postgres_ok:
        return False, postgres_reason

    settings = get_settings()
    qdrant_url = urlsplit(settings.qdrant_url)
    qdrant_host, qdrant_port = qdrant_url.hostname or "localhost", qdrant_url.port or 6333
    if not _tcp_reachable(qdrant_host, qdrant_port):
        return False, f"qdrant unreachable at {qdrant_host}:{qdrant_port}"

    return True, ""


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip `integration`-marked tests when the real infra isn't reachable, and
    `db`-marked tests when Postgres alone isn't reachable.

    Uses `get_closest_marker` rather than `item.keywords`: keyword matching
    also matches on substrings of the test's node id (e.g. a `tests/db/`
    package name), which would spuriously skip unmarked tests that merely
    live under a `db`-named directory.
    """
    postgres_ok, postgres_reason = _postgres_reachable()
    infra_ok, infra_reason = _infra_reachable(postgres_ok, postgres_reason)
    integration_skip = pytest.mark.skip(reason=f"integration infra unreachable: {infra_reason}")
    db_skip = pytest.mark.skip(reason=f"db infra unreachable: {postgres_reason}")
    for item in items:
        if not infra_ok and item.get_closest_marker("integration") is not None:
            item.add_marker(integration_skip)
        if not postgres_ok and item.get_closest_marker("db") is not None:
            item.add_marker(db_skip)
