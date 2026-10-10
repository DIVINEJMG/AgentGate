from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.application.services.cutover import CutoverStage
from app.infrastructure.ai.connections import DEFAULT_CONNECTIONS, TextConnection
from app.infrastructure.ai.model_profiles import PlannerModelProfile


class Settings(BaseSettings):
    github_login_enabled: bool = False
    github_login_callback_url: str = ""
    github_expanded_enabled: bool = False
    github_events_enabled: bool = False
    coding_execution_enabled: bool = False
    github_app_id: str = ""
    github_app_slug: str = ""
    github_client_id: str = ""
    github_client_secret: SecretStr | None = None
    github_app_private_key: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    github_api_version: str = "2026-03-10"
    github_callback_url: str = ""
    github_frontend_url: str = "http://localhost:5173"
    e2b_api_key: SecretStr | None = None
    coding_template_id: str = ""
    coding_allowed_hosts: str = "registry.npmjs.org,pypi.org,files.pythonhosted.org"
    coding_global_concurrency: int = Field(default=2, ge=1, le=100)
    coding_organization_concurrency: int = Field(default=1, ge=1, le=20)
    coding_task_active_seconds: int = Field(default=1800, ge=60, le=14400)
    coding_organization_daily_seconds: int = Field(default=7200, ge=60)
    coding_idle_seconds: int = Field(default=300, ge=60)
    coding_initialization_seconds: int = Field(default=600, ge=60, le=1800)
    coding_retention_seconds: int = Field(default=86400, ge=300)
    integration_foundation_enabled: bool = False
    integration_auth_callback_base_url: str = ""
    integration_organization_rate_per_minute: int = Field(default=60, ge=1, le=10000)
    integration_connection_rate_per_minute: int = Field(default=20, ge=1, le=10000)
    integration_organization_concurrency: int = Field(default=4, ge=1, le=100)
    integration_connection_concurrency: int = Field(default=2, ge=1, le=50)
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
    qstash_runtime_execute_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "UPSTASH_QSTASH_RUNTIME_EXECUTE_URL",
            "QSTASH_RUNTIME_EXECUTE_URL",
        ),
    )
    qstash_runtime_sweep_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "UPSTASH_QSTASH_RUNTIME_SWEEP_URL",
            "QSTASH_RUNTIME_SWEEP_URL",
        ),
    )
    runtime_delivery_timeout_seconds: int = 120
    runtime_planner_timeout_seconds: int = Field(default=2000, ge=15)
    runtime_qstash_parallelism: int = 4
    runtime_sweep_limit: int = 50
    legacy_database_url: SecretStr | None = None
    migration_execution_enabled: bool = False
    database_migrate_on_startup: bool = False
    integration_encryption_key: SecretStr | None = None
    oidc_client_secret: SecretStr | None = None
    ai_enabled: bool = False
    ai_provider: str = "nvidia_nim"
    ai_provider_base_url: str = "https://integrate.api.nvidia.com/v1"
    ai_provider_api_key: SecretStr | None = None
    ai_coordinator_api_key: SecretStr | None = None
    ai_vision_api_key: SecretStr | None = None
    ai_coordinator_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    ai_vision_model: str = "nvidia/ising-calibration-1.5-31b"
    ai_timeout_seconds: int = 60
    ai_max_retries: int = 2
    ai_max_output_tokens: int = 1800
    smart_planner_enabled: bool = False
    ai_interactive_timeout_seconds: int = Field(default=1500, ge=1)
    ai_interactive_attempt_seconds: int = Field(default=250, ge=1)
    # Retained for configuration compatibility; the saved decision deadline controls inference.
    smart_planner_attempt_seconds: int = Field(default=1500, ge=1)
    smart_planner_output_tokens: int = Field(default=1800, ge=16)
    smart_planner_model_profiles: dict[str, "PlannerModelProfile"] = {}
    ai_interactive_routes: list[str] = [
        "nvidia_nim:openai/gpt-oss-20b",
        "groq:qwen/qwen3.8-27b", "nvidia_nim:configured",
        "openrouter:cohere/north-mini-code:free", "nvidia_nim:z-ai/glm-5.3",
        "nvidia_nim:poolside/laguna-xs-2.1", "nvidia_nim:moonshotai/kimi-k3",
    ]
    ai_complex_routes: list[str] = [
        "groq:qwen/qwen3.8-27b",
        "nvidia_nim:configured", "nvidia_nim:moonshotai/kimi-k3",
        "nvidia_nim:openai/gpt-oss-20b", "nvidia_nim:poolside/laguna-xs-2.1",
    ]
    ai_interactive_reserved_slots: int = Field(default=1, ge=0)
    ai_interactive_reserved_requests: int = Field(default=5, ge=0)
    ai_interactive_reserved_tokens: int = Field(default=500000, ge=0)
    ai_account_concurrency: int = Field(default=4, ge=1)
    smart_planner_routes: list[str] = [
        "nvidia_nim:moonshotai/kimi-k3",
        "nvidia_nim:z-ai/glm-5.3",
        "nvidia_nim:configured",
        "nvidia_nim:z-ai/glm-5.3-flash",
        "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia_nim:poolside/laguna-xs-2.1",
        "openrouter:cohere/north-mini-code:free",
        "openrouter:google/gemma-4-31b-it:free",
        "openrouter:nvidia/nemotron-3.5-lightning:free",
        "openrouter:google/gemma-4-26b-a4b-it:free",
        "nvidia_nim:openai/gpt-oss-20b",
        "openrouter:thinkingmachines/inkling:free",
    ]
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    hf_token: SecretStr | None = None
    ai_text_connections: dict[str, TextConnection] = DEFAULT_CONNECTIONS
    ai_text_api_keys: dict[str, SecretStr] = {}

    @field_validator("ai_text_connections")
    @classmethod
    def text_connection_names(cls, value):
        import re
        if any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in {"nvidia_nim", "openrouter"} for name in value):
            raise ValueError("Use unique text connection names; existing transport names are reserved.")
        return value

    openrouter_planner_data_allowed: bool = False
    openrouter_allowed_providers: list[str] = []
    smart_planner_org_concurrency: int = Field(default=2, ge=1)
    smart_planner_org_requests_per_minute: int = Field(default=20, ge=1)
    smart_planner_org_tokens_per_day: int = Field(default=50000000, ge=1)
    brave_search_api_key: SecretStr | None = None
    tavily_search_api_key: SecretStr | None = None
    qstash_web_research_url: str | None = None
    web_research_daily_limit_per_organization: int = 100
    web_research_hourly_limit_per_organization: int = 20
    web_research_provider_calls_per_minute: int = 30
    web_research_max_pages: int = 3
    runtime_max_action_steps: int = 20
    browser_idle_shutdown_seconds: int = 180
    browser_session_ttl_seconds: int = 900
    browser_max_active_sessions: int = 1
    browser_max_sessions_per_organization: int = 1
    browser_max_pages_per_session: int = 3
    browser_memory_soft_limit_percent: int = 85
    browser_memory_hard_limit_percent: int = 90
    browser_http_read_max_bytes: int = 1_000_000
    browser_http_read_timeout_seconds: int = 12
    browser_cold_start_timeout_seconds: int = 65
    browser_action_timeout_seconds: int = 45
    browser_launch_min_headroom_bytes: int = 256 * 1024 * 1024
    browser_runtime_retry_limit: int = 4
    browser_runtime_retry_backoff_seconds: int = 20
    runtime_provider_retry_limit: int = 2
    runtime_provider_retry_backoff_seconds: int = 5
    conversation_attachment_max_bytes: int = 10 * 1024 * 1024
    conversation_attachment_text_max_chars: int = 100_000
    vision_signed_url_ttl_seconds: int = 300
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    # Signed read-only data for Audoryn Console; see docs/integration/console-admin-reads-plan.md.
    console_reads_enabled: bool = False
    console_reads_audience: str = "agentgate"
    console_reads_key_id: str | None = None
    console_reads_key_secret: SecretStr | None = None
    console_reads_key_scopes: str = ""
    console_reads_previous_key_id: str | None = None
    console_reads_previous_key_secret: SecretStr | None = None
    console_reads_previous_key_scopes: str = ""
    console_reads_clock_skew_seconds: int = 60
    console_reads_rate_per_minute: int = 120
    console_reads_statement_timeout_ms: int = 3000

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
