import pytest

from app.bootstrap.settings import Settings


def test_production_cors_rejects_wildcard() -> None:
    settings = Settings(environment="production", cors_allowed_origins=("*",))
    with pytest.raises(ValueError, match="Wildcard CORS"):
        settings.validate_security()


def test_production_cors_accepts_explicit_origins() -> None:
    settings = Settings(
        environment="production",
        cors_allowed_origins=("https://audoryn.com", "https://www.audoryn.com"),
    )
    settings.validate_security()


def test_secret_values_are_not_revealed_by_repr() -> None:
    settings = Settings(
        database_url="postgresql://user:password@host/db",
        redis_url="redis://:password@host:6379/0",
    )
    rendered = repr(settings)
    assert "password@host" not in rendered
    assert "**********" in rendered
