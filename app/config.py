"""Application configuration.

Loads and validates all environment-derived settings for the Adaptive Coding
Agent using Pydantic v2 / pydantic-settings. Validation is fail-fast: the
process must refuse to start if required configuration is missing or
malformed, and error messages must never leak secret values.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
OPENROUTER_DEFAULT_MODEL = "qwen/qwen3.8-27b:free"
GROQ_DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b"
OPENROUTER_DEFAULT_VISION_MODEL = "qwen/qwen3.8-27b:free"

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

    llm_provider: Literal["groq", "openrouter"] = "groq"
    llm_model: str | None = None
    llm_vision_model: str | None = None
    groq_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "adaptive-coding-agent"

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8501"]
    )

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
        "openrouter_api_key",
        "llm_model",
        "llm_vision_model",
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
        if self.llm_provider == "groq" and self.groq_api_key is None:
            raise ValueError("GROQ_API_KEY is required when LLM_PROVIDER=groq")
        if self.llm_provider == "openrouter" and self.openrouter_api_key is None:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter")
        return self

    @property
    def resolved_llm_model(self) -> str:
        """The effective model name: explicit override or provider default."""
        if self.llm_model is not None:
            return self.llm_model
        if self.llm_provider == "groq":
            return GROQ_DEFAULT_MODEL
        return OPENROUTER_DEFAULT_MODEL

    @property
    def resolved_llm_vision_model(self) -> str:
        """The effective vision model name: explicit override or provider default."""
        if self.llm_vision_model is not None:
            return self.llm_vision_model
        if self.llm_provider == "groq":
            return GROQ_DEFAULT_VISION_MODEL
        return OPENROUTER_DEFAULT_VISION_MODEL

    @property
    def llm_api_key(self) -> SecretStr:
        """The API key for the currently selected LLM provider."""
        if self.llm_provider == "groq":
            if self.groq_api_key is None:
                raise RuntimeError("GROQ_API_KEY is not configured")
            return self.groq_api_key
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
