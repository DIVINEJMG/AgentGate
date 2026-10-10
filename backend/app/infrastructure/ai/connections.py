"""Named, backend-configured text endpoints; never selected from model output."""

from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.ai.providers import AIProviderError


class TextConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_format: str = "openai_chat"
    base_url: str
    hosting_provider: str
    credential_ref: str
    schema_mode: str = "prompt_json"
    enabled: bool = False
    data_allowed: bool = False
    billing_allowed: bool = False
    billing_required: bool = False

    @field_validator("base_url")
    @classmethod
    def public_https_endpoint(cls, value):
        import ipaddress

        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.port not in {None, 443}
        ):
            raise ValueError("Use a credential-free public HTTPS API base URL.")
        host = parsed.hostname.lower().rstrip(".")
        if "." not in host or host.endswith((".local", ".internal", ".localhost")):
            raise ValueError("Private endpoints are not supported.")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if not address.is_global:
                raise ValueError("Private endpoints are not supported.")
        return value.rstrip("/")

    @field_validator("hosting_provider", "credential_ref")
    @classmethod
    def explicit_identity(cls, value):
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) or value in {
            "auto",
            "fastest",
            "cheapest",
        }:
            raise ValueError("Use an explicit provider or credential reference.")
        return value

    @field_validator("api_format")
    @classmethod
    def supported_api(cls, value):
        if value != "openai_chat":
            raise ValueError("Only the OpenAI-compatible chat completion format is implemented.")
        return value

    @field_validator("schema_mode")
    @classmethod
    def supported_schema(cls, value):
        if value not in {"prompt_json", "json_object", "groq_strict", "json_schema"}:
            raise ValueError("Unknown endpoint schema compatibility mode.")
        return value


DEFAULT_CONNECTIONS = {
    "groq": TextConnection(
        base_url="https://api.groq.com/openai/v1",
        hosting_provider="groq",
        credential_ref="groq",
        schema_mode="groq_strict",
    ),
    "hf_featherless": TextConnection(
        base_url="https://router.huggingface.co/v1",
        hosting_provider="featherless-ai",
        credential_ref="huggingface",
        billing_required=True,
    ),
    "hf_novita": TextConnection(
        base_url="https://router.huggingface.co/v1",
        hosting_provider="novita",
        credential_ref="huggingface",
        billing_required=True,
    ),
}


def route_credential(route, config):
    name = route["provider"]
    if name == "openrouter":
        secret = config.openrouter_api_key
    elif name == "nvidia_nim":
        secret = config.ai_coordinator_api_key or config.ai_provider_api_key
    else:
        connection = route.get("connection") or getattr(config, "ai_text_connections", {}).get(name)
        if not connection:
            return None
        connection = TextConnection.model_validate(connection)
        ref = connection.credential_ref
        secret = (
            config.groq_api_key
            if ref == "groq"
            else config.hf_token
            if ref == "huggingface"
            else config.ai_text_api_keys.get(ref)
        )
    return secret.get_secret_value() if secret else None


def snapshot_connection(route, config):
    name = route["provider"]
    if name in {"nvidia_nim", "openrouter"}:
        route["endpoint"] = (
            config.ai_provider_base_url.rstrip("/")
            if name == "nvidia_nim"
            else "https://openrouter.ai/api/v1"
        ) + "/chat/completions"
        route["hostingProvider"] = name
        route["requestedModel"] = route["model"]
    else:
        connection = getattr(config, "ai_text_connections", {}).get(name)
        if connection:
            route["connection"] = connection.model_dump()
            route["endpoint"] = connection.base_url + "/chat/completions"
            route["hostingProvider"] = connection.hosting_provider
            route["requestedModel"] = wire_model(connection, route["model"])
    return route


def wire_model(connection, model):
    if urlsplit(connection.base_url).hostname == "router.huggingface.co":
        if ":" in model:
            raise AIProviderError(
                "configuration_missing",
                "Use the Hub model ID without automatic routing suffixes.",
                retryable=False,
            )
        return f"{model}:{connection.hosting_provider}"
    return model


def transport_identity(route):
    return {
        key: route[key] for key in ("endpoint", "hostingProvider", "requestedModel") if key in route
    }


def route_account_name(route, config):
    connection = route.get("connection") or getattr(config, "ai_text_connections", {}).get(
        route["provider"]
    )
    if connection:
        connection = TextConnection.model_validate(connection)
        # Two HF hosts using one token share admission and account cooldowns.
        host = urlsplit(connection.base_url).hostname
        return f"{host}:{connection.credential_ref}"
    return route["provider"]
