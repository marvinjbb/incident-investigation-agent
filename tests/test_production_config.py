import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_rejects_development_defaults() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", _env_file=None)


def test_production_accepts_explicit_safe_configuration() -> None:
    settings = Settings(
        environment="production",
        postgres_password="not-the-development-password",
        openai_api_key="configured-at-runtime",
        cors_allowed_origins="https://marvinjb.dev",
        trusted_hosts="api.marvinjb.dev",
        expose_internal_routes=False,
        _env_file=None,
    )

    assert settings.allowed_origins == ["https://marvinjb.dev"]
    assert settings.allowed_hosts == ["api.marvinjb.dev"]


def test_production_rejects_wildcard_cors() -> None:
    with pytest.raises(ValidationError):
        Settings(
            environment="production",
            postgres_password="not-the-development-password",
            openai_api_key="configured-at-runtime",
            cors_allowed_origins="*",
            expose_internal_routes=False,
            _env_file=None,
        )


def test_production_rejects_multiple_uvicorn_workers() -> None:
    with pytest.raises(ValidationError, match="exactly one Uvicorn worker"):
        Settings(
            environment="production",
            postgres_password="not-the-development-password",
            openai_api_key="configured-at-runtime",
            cors_allowed_origins="https://marvinjb.dev",
            expose_internal_routes=False,
            uvicorn_workers=2,
            _env_file=None,
        )
