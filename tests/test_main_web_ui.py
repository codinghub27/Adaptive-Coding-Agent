"""Tests for the web-UI integration pieces of `app.main`: CORS credential
gating (a wildcard origin must never combine with `allow_credentials=True`)
and the conditional `StaticFiles` mount of the built frontend, which must
never be able to shadow an API route (`app.main.create_app` registers it
last, after every router and `/health`).
"""

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import Mount
from starlette.testclient import TestClient

import app.main as main_module
from app.config import Settings
from app.main import create_app

MakeSettings = Callable[..., Settings]


def test_allow_credentials_is_off_when_cors_origins_contains_wildcard(
    make_settings: MakeSettings,
) -> None:
    app = create_app(make_settings(cors_origins=["*"]))
    middlewares = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    (cors,) = middlewares
    assert cors.kwargs["allow_credentials"] is False


def test_allow_credentials_is_on_for_explicit_origins(make_settings: MakeSettings) -> None:
    app = create_app(make_settings(cors_origins=["http://127.0.0.1:5173"]))
    middlewares = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    (cors,) = middlewares
    assert cors.kwargs["allow_credentials"] is True


def test_no_static_mount_when_frontend_dist_dir_is_missing(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    missing_dir = tmp_path / "does-not-exist"
    app = create_app(make_settings(frontend_dist_dir=missing_dir))
    assert not any(isinstance(route, Mount) and route.path == "" for route in app.routes)


def test_static_mount_serves_index_without_shadowing_health(
    monkeypatch: pytest.MonkeyPatch, make_settings: MakeSettings, tmp_path: Path
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")

    settings = make_settings(frontend_dist_dir=dist_dir)

    async def _fake_ping_db(*_args: object, **_kwargs: object) -> bool:
        return True

    async def _fake_ping_qdrant(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(main_module, "ping_db", _fake_ping_db)
    monkeypatch.setattr(main_module, "ping_qdrant", _fake_ping_qdrant)

    with TestClient(create_app(settings)) as client:
        index_response = client.get("/")
        health_response = client.get("/health")

    assert index_response.status_code == 200
    assert "hi" in index_response.text

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok", "db": "ok", "qdrant": "ok"}
