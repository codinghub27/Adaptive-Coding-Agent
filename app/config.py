"""Application configuration.

Loads and validates all environment-derived settings for the Adaptive Coding
Agent using Pydantic v2 / pydantic-settings. Validation is fail-fast: the
process must refuse to start if required configuration is missing or
malformed, and error messages must never leak secret values.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.knowledge.base import DEFAULT_KNOWLEDGE_TOP_K

LLMProvider = Literal["groq", "openrouter"]

GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
#: Free-tier OpenRouter defaults, chosen by probing the live model list on
#: 2026-09-26: both answered a real request, while `qwen/qwen3.8-27b:free` and
#: `google/gemma-4-31b-it:free` returned 429 (free-tier rate limit) and
#: `deepseek/deepseek-chat-v3-0324:free` returned 404. Free model availability
#: rotates, so treat these as a working default, not a guarantee -- override
#: with `LLM_MODEL` / `LLM_VISION_MODEL` if one starts refusing.
OPENROUTER_DEFAULT_MODEL = "nvidia/nemotron-3.5-lightning:free"
GROQ_DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"
#: Must accept image input: this one reports `text,image` modalities.
OPENROUTER_DEFAULT_VISION_MODEL = "dots-studio/dots-3-note-preview:free"

_ASYNCPG_SCHEME = "postgresql+asyncpg"
_NORMALIZABLE_SCHEMES = (
    "postgresql",
    "postgres",
    "postgresql+psycopg",
    "postgresql+psycopg2",
    "postgresql+asyncpg",
)


def normalize_database_url(value: str) -> str:
    """Normalize a libpq-style Postgres URL to the asyncpg driver form.

    Rewrites the scheme to `postgresql+asyncpg://` (accepting `postgresql://`,
    `postgres://`, `postgresql+psycopg://`, `postgresql+psycopg2://`, and
    `postgresql+asyncpg://` itself) and translates the libpq `sslmode=<v>`
    query parameter to asyncpg's `ssl=<v>`, leaving all other query
    parameters untouched.
    """
    url = make_url(value)
    if url.drivername not in _NORMALIZABLE_SCHEMES:
        raise ValueError(
            "database_url must use one of the postgresql schemes "
            "(postgresql://, postgres://, postgresql+psycopg://, "
            "postgresql+psycopg2://, postgresql+asyncpg://)"
        )

    query = dict(url.query)
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")

    url = url.set(drivername=_ASYNCPG_SCHEME, query=query)
    return url.render_as_string(hide_password=False)


def _split_csv(value: list[str] | str) -> list[str]:
    """Split a comma-separated env string into a list; pass lists through."""
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _empty_str_to_none(value: object) -> object:
    """Treat an empty/whitespace-only string as None."""
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


class Settings(BaseSettings):
    """Typed, validated application settings loaded from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    app_env: Literal["dev", "test", "prod"] = "dev"

    database_url: str = Field(repr=False)

    qdrant_url: str
    qdrant_api_key: SecretStr | None = None
    qdrant_timeout: int = 10

    #: Name of the Qdrant collection holding the knowledge-corpus chunks.
    knowledge_collection: str = "dsa_knowledge"
    #: fastembed dense-embedding model used to embed chunks and queries.
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    #: Expected output dimensionality of `embedding_model`.
    embedding_dim: int = Field(default=384, ge=1)
    #: fastembed cross-encoder model used to rerank retrieved chunks.
    reranker_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    #: Directory fastembed caches downloaded models in; None uses its default.
    fastembed_cache_dir: str | None = None
    #: Number of chunks returned by knowledge retrieval per query.
    knowledge_top_k: int = Field(default=DEFAULT_KNOWLEDGE_TOP_K, ge=1, le=20)
    #: Wall-clock budget for a single knowledge retrieval call.
    knowledge_timeout_s: float = Field(default=3.0, gt=0, le=30)
    #: Wall-clock budget for building the `KnowledgeRetriever` at startup
    #: (embedder/reranker model load, including a first-time download). A
    #: startup that blows this budget degrades to `retriever=None` rather
    #: than hanging the process -- see `app.main`'s lifespan.
    knowledge_startup_timeout_s: float = Field(default=60.0, gt=0, le=600)
    #: Whether the app builds a `KnowledgeRetriever` at startup. Startup must
    #: never fail because of knowledge retrieval -- this is also the escape
    #: hatch tests use to skip the (multi-second) embedder/reranker model load.
    knowledge_enabled: bool = True

    llm_provider: LLMProvider = "openrouter"
    llm_model: str | None = None
    #: When set, every HTTP attempt against the LLM provider is appended
    #: here as one JSON line: status, seconds, rate-limit headers. Metadata
    #: only (see `app.llm.telemetry`). Off by default.
    llm_http_log_path: Path | None = None
    #: Optional smaller/faster Groq model for intent classification only.
    #: Groq's rate limits are per model, so this also gives the classifier
    #: its own token budget. Unset (the default): the main model classifies.
    llm_classifier_model: str | None = None
    llm_vision_model: str | None = None
    groq_api_key: SecretStr | None = None
    #: Additional Groq keys (`GROQ_API_KEY_1` .. `GROQ_API_KEY_4`), tried in
    #: this order after `groq_api_key` when the key in use is rate-limited.
    #: Groq's free tier is per-key, so several keys is the supported way to
    #: keep a session going; see `llm_failover_chain`.
    groq_api_key_1: SecretStr | None = None
    groq_api_key_2: SecretStr | None = None
    groq_api_key_3: SecretStr | None = None
    groq_api_key_4: SecretStr | None = None
    groq_api_key_5: SecretStr | None = None
    groq_api_key_6: SecretStr | None = None
    groq_api_key_7: SecretStr | None = None
    groq_api_key_8: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "adaptive-coding-agent"
    #: LangSmith API base URL. `None` uses the SDK default (US cloud); an EU
    #: or self-hosted workspace MUST set this, or every run is posted to a
    #: region that rejects the key and nothing appears in the UI.
    langsmith_endpoint: str | None = None

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://127.0.0.1:5173", "http://localhost:5173"]
    )

    #: Directory containing the built web UI (`pnpm build` output inside
    #: `frontend/`), resolved relative to the process's current working
    #: directory. `app.main.create_app` mounts it as `StaticFiles` at `/`
    #: only when it exists and is a directory -- absent (the normal state in
    #: tests and before a build) means no static mount at all.
    frontend_dist_dir: Path = Path("frontend/dist")
    #: Whether the httpOnly refresh cookie (`app.auth.routes`) carries the
    #: `Secure` attribute. MUST be true in any deployment served over HTTPS;
    #: false is only appropriate for local `http://localhost` development.
    refresh_cookie_secure: bool = False

    #: Whether the app builds a `SandboxRunner` at startup. Startup must
    #: never fail because Docker isn't reachable -- this is also the escape
    #: hatch tests use to skip the (Docker-dependent) sandbox build.
    sandbox_enabled: bool = True
    #: Tag of the locked-down sandbox image (see `docker/sandbox.Dockerfile`).
    sandbox_image: str = "aca-sandbox:py3.11-v1"
    #: Memory limit applied to every sandbox container.
    sandbox_memory_mb: int = Field(256, ge=64, le=2048)
    #: Max sandbox containers running at once; runs beyond this queue.
    sandbox_max_concurrent: int = Field(4, ge=1, le=32)
    #: Wall-clock budget a run may wait queued for a free sandbox slot
    #: before being rejected as busy.
    sandbox_queue_timeout_s: float = Field(30.0, gt=0, le=300)
    #: Wall-clock budget for the startup Docker-reachability probe (client
    #: construction, ping, image check). A hung/slow Docker daemon must
    #: degrade to `runner=None` rather than block app startup -- see
    #: `app.execution.runner.build_sandbox_runner`.
    sandbox_startup_timeout_s: float = Field(15.0, gt=0, le=120)

    jwt_secret_key: SecretStr = Field(repr=False)
    jwt_algorithm: Literal["HS256"] = "HS256"
    access_token_expire_minutes: int = Field(default=45, ge=1, le=1440)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)
    #: Clock-skew tolerance applied to `iat`/`exp` validation when decoding a token.
    jwt_leeway_seconds: int = Field(default=10, ge=0, le=120)
    #: Window after a refresh token is rotated during which presenting the
    #: rotated-away token again is treated as a benign duplicate (e.g. a
    #: retried request or a duplicate tab) rather than reuse detection.
    refresh_reuse_grace_seconds: int = Field(default=10, ge=0, le=300)

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)

    @field_validator("jwt_secret_key")
    @classmethod
    def _validate_jwt_secret_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError("jwt_secret_key must be at least 32 characters")
        return value

    @field_validator("qdrant_timeout")
    @classmethod
    def _validate_qdrant_timeout(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("qdrant_timeout must be greater than 0")
        return value

    @field_validator(
        "qdrant_api_key",
        "groq_api_key",
        "groq_api_key_1",
        "groq_api_key_2",
        "groq_api_key_3",
        "groq_api_key_4",
        "groq_api_key_5",
        "groq_api_key_6",
        "groq_api_key_7",
        "groq_api_key_8",
        "openrouter_api_key",
        "llm_model",
        "llm_vision_model",
        "fastembed_cache_dir",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return _empty_str_to_none(value)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: list[str] | str) -> list[str]:
        return _split_csv(value)

    @model_validator(mode="after")
    def _require_active_provider_key(self) -> "Settings":
        if self.llm_provider == "groq" and not self.groq_api_keys:
            raise ValueError(
                "GROQ_API_KEY (or GROQ_API_KEY_1..8) is required when LLM_PROVIDER=groq"
            )
        if self.llm_provider == "openrouter" and self.openrouter_api_key is None:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
        return self

    @property
    def groq_api_keys(self) -> list[SecretStr]:
        """Every configured Groq key, in failover order, de-duplicated.

        `GROQ_API_KEY` first (when set), then `GROQ_API_KEY_1` .. `_8`. A key
        repeated across two variables is kept once: retrying the same
        credential after it was rate-limited only spends another failed call.
        """
        keys: list[SecretStr] = []
        seen: set[str] = set()
        for key in (
            self.groq_api_key,
            self.groq_api_key_1,
            self.groq_api_key_2,
            self.groq_api_key_3,
            self.groq_api_key_4,
            self.groq_api_key_5,
            self.groq_api_key_6,
            self.groq_api_key_7,
            self.groq_api_key_8,
        ):
            if key is None:
                continue
            secret = key.get_secret_value()
            if not secret or secret in seen:
                continue
            seen.add(secret)
            keys.append(key)
        return keys

    @property
    def llm_failover_chain(self) -> list[tuple[LLMProvider, SecretStr]]:
        """Provider/key pairs to try in order when one is rate-limited.

        The configured `llm_provider`'s credentials come first, then the other
        provider's as a last resort: exhausting every Groq key should degrade
        to OpenRouter rather than fail the turn. Empty only if nothing at all
        is configured -- `_require_active_provider_key` already rejects that
        for the active provider.
        """
        groq: list[tuple[LLMProvider, SecretStr]] = [("groq", key) for key in self.groq_api_keys]
        openrouter: list[tuple[LLMProvider, SecretStr]] = (
            [("openrouter", self.openrouter_api_key)] if self.openrouter_api_key is not None else []
        )
        if self.llm_provider == "groq":
            return groq + openrouter
        return openrouter + groq

    def model_for(self, provider: LLMProvider) -> str:
        """The chat model to use with `provider`.

        An explicit `LLM_MODEL` names a model on the *configured* provider, so
        it is deliberately not carried over to a fallback on the other one: a
        Groq model id means nothing to OpenRouter, and passing it across would
        turn a rate-limit failover into a 404.
        """
        if provider == self.llm_provider and self.llm_model is not None:
            return self.llm_model
        return GROQ_DEFAULT_MODEL if provider == "groq" else OPENROUTER_DEFAULT_MODEL

    def vision_model_for(self, provider: LLMProvider) -> str:
        """The vision model to use with `provider`; see `model_for`."""
        if provider == self.llm_provider and self.llm_vision_model is not None:
            return self.llm_vision_model
        return GROQ_DEFAULT_VISION_MODEL if provider == "groq" else OPENROUTER_DEFAULT_VISION_MODEL

    @property
    def resolved_llm_model(self) -> str:
        """The effective model name: explicit override or provider default."""
        return self.model_for(self.llm_provider)

    @property
    def resolved_llm_vision_model(self) -> str:
        """The effective vision model name: explicit override or provider default."""
        return self.vision_model_for(self.llm_provider)

    @property
    def llm_api_key(self) -> SecretStr:
        """The first API key for the currently selected LLM provider.

        This is the credential the failover chain starts from; the rest are in
        `llm_failover_chain`.
        """
        if self.llm_provider == "groq":
            keys = self.groq_api_keys
            if not keys:
                raise RuntimeError("GROQ_API_KEY is not configured")
            return keys[0]
        if self.openrouter_api_key is None:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        return self.openrouter_api_key


class DatabaseSettings(BaseSettings):
    """Minimal settings needed to connect to the database (e.g. for Alembic).

    Loads only `DATABASE_URL` from the environment, so migrations don't need
    the LLM/Qdrant/LangSmith configuration required by the full `Settings`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
        case_sensitive=False,
    )

    database_url: str = Field(repr=False)

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        return normalize_database_url(value)


@lru_cache
def get_settings() -> Settings:
    """Return a cached, validated Settings instance."""
    return Settings()  # pyright: ignore[reportCallIssue]


@lru_cache
def get_database_settings() -> DatabaseSettings:
    """Return a cached, validated DatabaseSettings instance."""
    return DatabaseSettings()  # pyright: ignore[reportCallIssue]
