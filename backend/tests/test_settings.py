from app.bootstrap.settings import Settings


def test_render_postgres_url_is_normalized_for_async_sqlalchemy() -> None:
    settings = Settings(database_url="postgresql://user:pass@host/database")
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_legacy_postgres_scheme_is_normalized() -> None:
    settings = Settings(database_url="postgres://user:pass@host/database")
    assert settings.database_url.startswith("postgresql+asyncpg://")
