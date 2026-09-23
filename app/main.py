"""FastAPI application factory and the `/health` endpoint.

Settings are resolved inside `create_app()` at construction time so that a
missing or invalid required environment variable raises a `ValidationError`
(naming the offending field) as soon as the app is built, rather than lazily
at first use.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory, ping_db
from app.health import ping_qdrant
from app.input.api import MAX_REQUEST_BYTES, BodySizeLimitMiddleware
from app.input.api import router as input_router
from app.llm import get_llm_client
from app.schemas import HealthResponse


def _get_engine(app: FastAPI) -> AsyncEngine:
    engine = app.state.engine
    if not isinstance(engine, AsyncEngine):
        raise RuntimeError("engine is not configured on app.state")
    return engine


def _get_qdrant(app: FastAPI) -> AsyncQdrantClient:
    qdrant = app.state.qdrant
    if not isinstance(qdrant, AsyncQdrantClient):
        raise RuntimeError("qdrant is not configured on app.state")
    return qdrant


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with AsyncExitStack() as stack:
            engine = create_engine(settings)
            stack.push_async_callback(engine.dispose)

            session_factory = create_session_factory(engine)
            qdrant = AsyncQdrantClient(
                url=settings.qdrant_url,
                api_key=(
                    settings.qdrant_api_key.get_secret_value()
                    if settings.qdrant_api_key is not None
                    else None
                ),
                timeout=settings.qdrant_timeout,
            )
            stack.push_async_callback(qdrant.close)

            app.state.settings = settings
            app.state.engine = engine
            app.state.session_factory = session_factory
            app.state.qdrant = qdrant
            app.state.llm = get_llm_client(settings)

            yield

    app = FastAPI(
        title="Adaptive Coding Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=False,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)

    app.include_router(input_router)

    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> JSONResponse:
        engine = _get_engine(request.app)
        qdrant = _get_qdrant(request.app)
        db_ok, qdrant_ok = await asyncio.gather(ping_db(engine), ping_qdrant(qdrant))

        body = HealthResponse(
            status="ok" if (db_ok and qdrant_ok) else "degraded",
            db="ok" if db_ok else "error",
            qdrant="ok" if qdrant_ok else "error",
        )
        status_code = 200 if (db_ok and qdrant_ok) else 503
        return JSONResponse(content=body.model_dump(), status_code=status_code)

    return app


app = create_app()
