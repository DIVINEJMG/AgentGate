from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.application.services.cutover import CutoverStage


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: str = "development"
    service_name: str = "audoryn-api"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://audoryn:audoryn@localhost:5432/audoryn"
    )
    redis_url: SecretStr = SecretStr("redis://localhost:6379/0")
    cors_allowed_origins: tuple[str, ...] = ("http://localhost:5173",)
    cutover_stage: CutoverStage = "system"
    shadow_mode_enabled: bool = True
    runtime_execution_enabled: bool = False
    worker_poll_seconds: float = 2.0
    outbox_poll_seconds: float = 1.0
    worker_heartbeat_ttl_seconds: int = 60
    auth_session_ttl_seconds: int = 60 * 60 * 24 * 7
    auth_login_failure_window_seconds: int = 60 * 15
    object_storage_provider: str = "unconfigured"
    upstash_blob_bucket: str | None = None
    upstash_blob_token: SecretStr | None = None
    qstash_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("UPSTASH_QSTASH_URL", "QSTASH_URL"),
    )
    qstash_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("UPSTASH_QSTASH_TOKEN", "QSTASH_TOKEN"),
    )
    qstash_current_signing_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "UPSTASH_QSTASH_CURRENT_SIGNING_KEY",
            "QSTASH_CURRENT_SIGNING_KEY",
        ),
    )
    qstash_next_signing_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "UPSTASH_QSTASH_NEXT_SIGNING_KEY",
            "QSTASH_NEXT_SIGNING_KEY",
        ),
    )
    qstash_failure_callback_url: str | None = None
    qstash_outbox_drain_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "UPSTASH_QSTASH_OUTBOX_DRAIN_URL",
            "QSTASH_OUTBOX_DRAIN_URL",
        ),
    )
    legacy_database_url: SecretStr | None = None
    migration_execution_enabled: bool = False
    integration_encryption_key: SecretStr | None = None
    oidc_client_secret: SecretStr | None = None
    model_provider_api_key: SecretStr | None = None
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if not isinstance(raw, str):
            return value
        if raw.startswith("postgres://"):
            raw = "postgresql+asyncpg://" + raw.removeprefix("postgres://")
        elif raw.startswith("postgresql://") and "+asyncpg" not in raw:
            raw = "postgresql+asyncpg://" + raw.removeprefix("postgresql://")

        # Neon emits libpq-oriented query parameters. SQLAlchemy's asyncpg
        # dialect needs `ssl` instead of `sslmode` and does not accept
        # `channel_binding` as a connect() keyword.
        raw = raw.replace("channel_binding=require&", "")
        raw = raw.replace("&channel_binding=require", "")
        raw = raw.replace("?channel_binding=require", "?")
        raw = raw.replace("sslmode=require", "ssl=require")
        return SecretStr(raw)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @property
    def database_dsn(self) -> str:
        return self.database_url.get_secret_value()

    @property
    def redis_dsn(self) -> str:
        return self.redis_url.get_secret_value()

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_security(self) -> None:
        if self.is_production and "*" in self.cors_allowed_origins:
            raise ValueError("Wildcard CORS is forbidden in production.")


@lru_cache
def get_settings() -> Settings:
    config = Settings()
    config.validate_security()
    return config


settings = get_settings()
