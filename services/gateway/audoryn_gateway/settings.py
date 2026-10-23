"""Gateway settings, read from `GATEWAY_*` environment variables. Defaults match the local harness
(docs/plans/architecture-scale-plan.md, section 7)."""
from __future__ import annotations

import json
from typing import Annotated

from pydantic import AnyHttpUrl, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from audoryn_gateway.limits import DEFAULT_POLICIES, Mode, Policy

Upstreams = Annotated[list[AnyHttpUrl], NoDecode]


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_", extra="ignore")

    # The copies of each service, comma-separated. The execution pool uses the core's copies until
    # the execution service runs on its own (Phase 3).
    core_upstreams: Upstreams = Field(default_factory=lambda: [AnyHttpUrl("http://127.0.0.1:8101")], min_length=1)
    execution_upstreams: Upstreams = Field(default_factory=list)
    connect_timeout_seconds: float = Field(default=5, gt=0, le=60)
    write_timeout_seconds: float = Field(default=60, gt=0, le=600)
    # No read timeout: the gateway must not cut off a slow or streaming reply that works today.
    max_connections: int = Field(default=200, ge=1, le=10_000)
    # Idle connections are kept on both sides only while neither may be closing them:
    # - to the copies, below their own keep-alive (uvicorn's 5 s), so the gateway never reuses a
    #   connection a copy is closing;
    # - from callers, above theirs (nginx's 75 s), so callers close idle connections first.
    upstream_keepalive_seconds: float = Field(default=3, gt=0, le=300)
    keep_alive_seconds: int = Field(default=75, ge=1, le=3600)
    # Rotation follows liveness ("the process is up and serving"), not the core's readiness: readiness
    # checks shared dependencies (Postgres, Redis, the AI provider) and can take seconds, so when one
    # of them is slow every copy would fail it at once and the gateway would turn all traffic away.
    health_path: str = "/health/live"
    health_interval_seconds: float = Field(default=5, gt=0, le=300)
    health_timeout_seconds: float = Field(default=5, gt=0, le=60)
    unhealthy_after: int = Field(default=2, ge=1, le=20)
    healthy_after: int = Field(default=2, ge=1, le=20)
    # Drains and restores copies from this machine only (`/gateway/admin/...`). Unset turns it off.
    admin_token: SecretStr | None = None
    # Shared by every gateway copy: the limit counters and the copies an admin drained. Unset keeps
    # drains in this process and turns the limits off.
    redis_url: SecretStr | None = None
    redis_timeout_seconds: float = Field(default=0.5, gt=0, le=10)
    # Every key the gateway writes starts with this, so environments sharing one Redis never mix.
    redis_prefix: str = Field(default="audoryn:gateway", pattern=r"^[A-Za-z0-9:_-]{1,64}$")
    limits_mode: Mode = "off"
    # Overrides by route group, as JSON: {"write": {"rules": [...], "max_body_bytes": ..., "mode": "enforce"}}.
    # A group named here replaces that group's defaults (limits.DEFAULT_POLICIES).
    limit_policies: dict[str, Policy] = Field(default_factory=dict)
    # How often the policies set in the Console are read from Redis (Phase 1e).
    policy_refresh_seconds: float = Field(default=10, gt=0, le=300)
    # Keys the session fingerprints, so tokens never reach Redis. Every gateway copy needs the same one.
    limit_secret: SecretStr | None = None
    # The edge (Phase 1d). CORS on here goes together with the core's CORS_ENABLED=false.
    cors_enabled: bool = False
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])
    security_headers: bool = False
    # Strict-Transport-Security, only once the public address is served over TLS; 0 sends none.
    hsts_seconds: int = Field(default=0, ge=0, le=63_072_000)
    log_level: str = "INFO"

    @property
    def policies(self) -> dict[str, Policy]:
        return {**DEFAULT_POLICIES, **self.limit_policies}

    @field_validator("core_upstreams", "execution_upstreams", "cors_allowed_origins", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        """A JSON list or a comma-separated list, as the core reads its own CORS_ALLOWED_ORIGINS."""
        if not isinstance(value, str):
            return value
        if value.strip().startswith("["):
            return json.loads(value)
        return [part.strip() for part in value.split(",") if part.strip()]
