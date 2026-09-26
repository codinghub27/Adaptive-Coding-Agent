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


def _write_bundle(dist: Path) -> None:
    """A minimal built bundle: the four pages Vite emits."""
    dist.mkdir(parents=True, exist_ok=True)
    for page in ("index", "login", "register", "chat"):
        (dist / f"{page}.html").write_text(f"<html><body>{page}</body></html>", encoding="utf-8")


def test_pages_are_served_at_extensionless_urls(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    """`/login`, `/register` and `/chat` serve their built pages, so the UI
    never has to link to a `.html` URL."""
    dist = tmp_path / "dist"
    _write_bundle(dist)
    app = create_app(make_settings(frontend_dist_dir=dist))

    with TestClient(app) as client:
        for page in ("login", "register", "chat"):
            response = client.get(f"/{page}")
            assert response.status_code == 200
            assert page in response.text


def test_html_urls_redirect_permanently_to_the_canonical_page(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    """An old `.html` bookmark lands on the canonical URL rather than leaving
    two live URLs for the same page."""
    dist = tmp_path / "dist"
    _write_bundle(dist)
    app = create_app(make_settings(frontend_dist_dir=dist))

    with TestClient(app) as client:
        for page in ("login", "register", "chat"):
            response = client.get(f"/{page}.html", follow_redirects=False)
            assert response.status_code == 301
            assert response.headers["location"] == f"/{page}"


def test_root_redirects_to_the_workspace(make_settings: MakeSettings, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_bundle(dist)
    app = create_app(make_settings(frontend_dist_dir=dist))

    with TestClient(app) as client:
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/chat"


def test_page_routes_never_shadow_an_api_route(make_settings: MakeSettings, tmp_path: Path) -> None:
    """The page routes and the catch-all mount are registered last, so the API
    still answers -- including the 401 on a protected route."""
    dist = tmp_path / "dist"
    _write_bundle(dist)
    app = create_app(make_settings(frontend_dist_dir=dist))

    with TestClient(app) as client:
        assert client.get("/health").json()["status"] in {"ok", "degraded"}
        assert client.get("/conversations").status_code == 401


def test_a_partial_bundle_registers_no_route_for_a_missing_page(
    make_settings: MakeSettings, tmp_path: Path
) -> None:
    """A dist without `chat.html` must not 500 on `/chat` or `/`; the page
    route is simply not registered."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "login.html").write_text("<html><body>login</body></html>", encoding="utf-8")
    app = create_app(make_settings(frontend_dist_dir=dist))

    with TestClient(app) as client:
        assert client.get("/login").status_code == 200
        assert client.get("/chat", follow_redirects=False).status_code == 404
