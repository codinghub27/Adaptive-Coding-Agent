"""Tests for `app.main` / `GET /health`."""

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.db import create_engine
from app.main import create_app
from tests.conftest import SETTINGS_ENV_VARS

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MakeSettings = Callable[..., Settings]


@pytest.mark.integration
def test_health_ok_with_real_infra() -> None:
    """Manual Test 1: GET /health with Postgres + Qdrant reachable."""
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "qdrant": "ok"}


def test_health_degraded_when_db_down(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    settings: Settings = make_settings()

    async def _fake_ping_db(*_args: object, **_kwargs: object) -> bool:
        return False

    async def _fake_ping_qdrant(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(main_module, "ping_db", _fake_ping_db)
    monkeypatch.setattr(main_module, "ping_qdrant", _fake_ping_qdrant)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert set(body.keys()) == {"status", "db", "qdrant"}
    assert body == {"status": "degraded", "db": "error", "qdrant": "ok"}


def test_health_degraded_when_qdrant_down(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    settings: Settings = make_settings()

    async def _fake_ping_db(*_args: object, **_kwargs: object) -> bool:
        return True

    async def _fake_ping_qdrant(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(main_module, "ping_db", _fake_ping_db)
    monkeypatch.setattr(main_module, "ping_qdrant", _fake_ping_qdrant)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert set(body.keys()) == {"status", "db", "qdrant"}
    assert body == {"status": "degraded", "db": "ok", "qdrant": "error"}


def test_main_import_fails_fast_without_required_env(tmp_path: Path) -> None:
    """Manual Test 2: starting the app with required env vars missing."""
    env = dict(os.environ)
    for key in SETTINGS_ENV_VARS:
        env.pop(key, None)
    env["PYTHONPATH"] = str(PROJECT_ROOT)

    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert "ValidationError" in result.stderr
    assert "database_url" in result.stderr


def test_lifespan_disposes_engine_when_qdrant_construction_fails(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings
) -> None:
    """A failure constructing the Qdrant client must still dispose the engine."""
    settings: Settings = make_settings()

    real_engine = create_engine(settings)
    disposed = False

    async def _fake_dispose(self: AsyncEngine, *args: object, **kwargs: object) -> None:
        nonlocal disposed
        disposed = True

    monkeypatch.setattr(AsyncEngine, "dispose", _fake_dispose)

    def _fake_create_engine(_settings: Settings) -> AsyncEngine:
        return real_engine

    monkeypatch.setattr(main_module, "create_engine", _fake_create_engine)

    class _RaisingQdrantClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("qdrant construction failed")

    monkeypatch.setattr(main_module, "AsyncQdrantClient", _RaisingQdrantClient)

    with pytest.raises(RuntimeError), TestClient(create_app(settings)):
        pass

    assert disposed is True


async def test_ping_qdrant_returns_false_for_unreachable_client() -> None:
    from qdrant_client import AsyncQdrantClient

    from app.health import ping_qdrant

    client = AsyncQdrantClient(url="http://127.0.0.1:1", timeout=1)
    try:
        result = await ping_qdrant(client, timeout_s=1.0)
        assert result is False
    finally:
        await client.close()
