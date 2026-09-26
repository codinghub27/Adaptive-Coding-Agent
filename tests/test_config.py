"""Tests for `app.config.Settings`."""

from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import ValidationError

from app.config import (
    GROQ_DEFAULT_MODEL,
    GROQ_DEFAULT_VISION_MODEL,
    OPENROUTER_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_VISION_MODEL,
    DatabaseSettings,
    Settings,
    normalize_database_url,
)

MakeSettings = Callable[..., Settings]


def test_missing_database_url_raises_validation_error() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            qdrant_url="http://127.0.0.1:1",
            groq_api_key="test-key",
        )
    errors = exc_info.value.errors()
    assert any(error["loc"] == ("database_url",) for error in errors)


def test_groq_provider_without_key_errors(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(groq_api_key=None, llm_provider="groq")
    assert "GROQ_API_KEY" in str(exc_info.value)


def test_openrouter_provider_without_key_errors(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(groq_api_key=None, llm_provider="openrouter", openrouter_api_key=None)
    assert "OPENROUTER_API_KEY" in str(exc_info.value)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+psycopg2://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
    ],
)
def test_database_url_normalization(make_settings: MakeSettings, raw: str, expected: str) -> None:
    settings = make_settings(database_url=raw)
    assert settings.database_url == expected


def test_unsupported_scheme_rejected_and_hides_password(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(database_url="mysql://u:secret-password@h:5432/d")
    assert "secret-password" not in str(exc_info.value)


def test_cors_origins_comma_split(make_settings: MakeSettings) -> None:
    settings = make_settings(cors_origins="http://a.example,http://b.example")
    assert settings.cors_origins == ["http://a.example", "http://b.example"]


@pytest.mark.parametrize(
    "field",
    ["qdrant_api_key", "groq_api_key", "openrouter_api_key", "llm_model", "llm_vision_model"],
)
def test_empty_string_keys_become_none(make_settings: MakeSettings, field: str) -> None:
    overrides: dict[str, object] = {field: ""}
    # Blanking the *active* provider's key would trip the "key required"
    # validator, so point the provider at the other one for that case. Both
    # directions are handled so this test does not depend on which provider
    # `Settings.llm_provider` currently defaults to.
    if field == "groq_api_key":
        overrides["llm_provider"] = "openrouter"
        overrides["openrouter_api_key"] = "test-key"
    elif field == "openrouter_api_key":
        overrides["llm_provider"] = "groq"
        overrides["groq_api_key"] = "test-key"
    settings = make_settings(**overrides)
    assert getattr(settings, field) is None


def test_resolved_llm_model_defaults_groq(make_settings: MakeSettings) -> None:
    settings = make_settings(llm_provider="groq")
    assert settings.resolved_llm_model == GROQ_DEFAULT_MODEL


def test_resolved_llm_model_defaults_openrouter(make_settings: MakeSettings) -> None:
    settings = make_settings(
        llm_provider="openrouter", openrouter_api_key="test-key", groq_api_key=None
    )
    assert settings.resolved_llm_model == OPENROUTER_DEFAULT_MODEL


def test_resolved_llm_model_override(make_settings: MakeSettings) -> None:
    settings = make_settings(llm_model="custom-model")
    assert settings.resolved_llm_model == "custom-model"


def test_resolved_llm_vision_model_defaults_groq(make_settings: MakeSettings) -> None:
    settings = make_settings(llm_provider="groq")
    assert settings.resolved_llm_vision_model == GROQ_DEFAULT_VISION_MODEL


def test_resolved_llm_vision_model_defaults_openrouter(make_settings: MakeSettings) -> None:
    settings = make_settings(
        llm_provider="openrouter", openrouter_api_key="test-key", groq_api_key=None
    )
    assert settings.resolved_llm_vision_model == OPENROUTER_DEFAULT_VISION_MODEL


def test_resolved_llm_vision_model_override(make_settings: MakeSettings) -> None:
    settings = make_settings(llm_vision_model="custom-vision-model")
    assert settings.resolved_llm_vision_model == "custom-vision-model"


def test_repr_hides_database_password_and_api_key(make_settings: MakeSettings) -> None:
    settings = make_settings(
        database_url="postgresql://u:super-secret-pw@h:5432/d",
        groq_api_key="super-secret-key",
    )
    representation = repr(settings)
    assert "super-secret-pw" not in representation
    assert "super-secret-key" not in representation


def test_qdrant_timeout_non_positive_rejected(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError):
        make_settings(qdrant_timeout=0)


def test_normalize_database_url_translates_sslmode_to_ssl() -> None:
    result = normalize_database_url("postgresql://u:p@h/db?sslmode=require&application_name=x")
    parts = urlsplit(result)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "postgresql+asyncpg://u:p@h/db"
    assert parse_qs(parts.query) == {"ssl": ["require"], "application_name": ["x"]}


def test_normalize_database_url_translates_sslmode_when_already_asyncpg() -> None:
    result = normalize_database_url("postgresql+asyncpg://u:p@h/db?sslmode=verify-full")
    parts = urlsplit(result)
    assert parts.scheme == "postgresql+asyncpg"
    assert parse_qs(parts.query) == {"ssl": ["verify-full"]}


def test_database_settings_works_without_llm_or_qdrant_vars() -> None:
    settings = DatabaseSettings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        database_url="postgresql://u:p@h:5432/d",
    )
    assert settings.database_url == "postgresql+asyncpg://u:p@h:5432/d"


def test_settings_env_isolation_allows_env_based_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The autouse env-isolation fixture must not break env-based loading."""
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.database_url == "postgresql+asyncpg://u:p@127.0.0.1:1/x"
    finally:
        get_settings.cache_clear()


def test_missing_jwt_secret_key_raises_validation_error(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(jwt_secret_key=None)
    errors = exc_info.value.errors()
    assert any(error["loc"] == ("jwt_secret_key",) for error in errors)


def test_short_jwt_secret_key_rejected_without_leaking_value(make_settings: MakeSettings) -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(jwt_secret_key="HS256")
    assert "HS256" not in str(exc_info.value)


def test_jwt_expiry_defaults(make_settings: MakeSettings) -> None:
    settings = make_settings()
    assert settings.access_token_expire_minutes == 45
    assert settings.refresh_token_expire_days == 7


def test_repr_hides_jwt_secret_key(make_settings: MakeSettings) -> None:
    settings = make_settings(jwt_secret_key="a-super-secret-jwt-signing-key-value")
    representation = repr(settings)
    assert "a-super-secret-jwt-signing-key-value" not in representation
