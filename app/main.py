"""FastAPI application factory and the `/health` endpoint.

Settings are resolved inside `create_app()` at construction time so that a
missing or invalid required environment variable raises a `ValidationError`
(naming the offending field) as soon as the app is built, rather than lazily
at first use.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory, ping_db
from app.execution.runner import build_sandbox_runner
from app.graph.api import router as chat_router
from app.health import ping_qdrant
from app.input.api import MAX_REQUEST_BYTES, BodySizeLimitMiddleware
from app.input.api import router as input_router
from app.knowledge.ingest import CorpusError
from app.knowledge.retrieve import create_retriever
from app.llm import get_llm_client
from app.llm.client import Tracer
from app.memory.api import router as memory_router
from app.schemas import HealthResponse

logger = logging.getLogger(__name__)


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

            if settings.knowledge_enabled:
                try:
                    app.state.retriever = await asyncio.wait_for(
                        create_retriever(settings, qdrant, Tracer.from_settings(settings)),
                        timeout=settings.knowledge_startup_timeout_s,
                    )
                except CorpusError:
                    # A malformed curated corpus is a code bug, not a runtime
                    # condition to degrade gracefully around -- fail startup
                    # loudly rather than silently starting with no knowledge
                    # retrieval (matches `create_retriever`'s docstring).
                    raise
                except Exception as exc:
                    app.state.retriever = None
                    logger.warning("knowledge retriever unavailable: %s", type(exc).__name__)
            else:
                app.state.retriever = None

            runner, close_runner = await build_sandbox_runner(settings)
            stack.callback(close_runner)
            app.state.runner = runner

            yield

    app = FastAPI(
        title="Adaptive Coding Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    # A wildcard origin combined with `allow_credentials=True` is both a CORS
    # spec violation (browsers reject it) and a real vulnerability (it would
    # let any site ride the user's cookies/Authorization header) -- so
    # credentials are only enabled when every configured origin is explicit.
    allow_credentials = "*" not in settings.cors_origins
    if not allow_credentials:
        logger.warning("cors_origins contains a wildcard entry: disabling allow_credentials")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=allow_credentials,
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_REQUEST_BYTES, path="/chat")

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """422s under `/auth/*` never echo request field values (e.g. a password
        or refresh token) back in the response body.

        FastAPI's default handler includes each Pydantic error's `input` (the
        raw, offending value) and `ctx`; for every other path that default
        behaviour is preserved unchanged. For `/auth/*`, each error is reduced
        to `loc`/`msg`/`type` only.
        """
        if not request.url.path.startswith("/auth/"):
            return await request_validation_exception_handler(request, exc)

        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    app.include_router(input_router)
    app.include_router(chat_router)
    app.include_router(auth_router)
    app.include_router(memory_router)

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

    # Mounted last, after every API router and `/health`: a mount at `/` is a
    # catch-all, so registering it any earlier could let it shadow an API
    # route. Only mounted when the built frontend actually exists -- the
    # normal state in tests and before a `pnpm build` is no mount at all.
    if settings.frontend_dist_dir.is_dir():
        app.mount(
            "/", StaticFiles(directory=settings.frontend_dist_dir, html=True), name="frontend"
        )
    else:
        logger.info("frontend bundle not built: no static mount registered")

    return app


app = create_app()
