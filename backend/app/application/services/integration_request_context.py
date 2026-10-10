"""Bounded conversation evidence; account authority is always resolved afresh."""
from typing import Any

from app.application.services.worker_memory import contains_secret_material


def integration_request_context(context: dict) -> dict:
    def clean(value: Any, depth: int = 0) -> Any:
        if depth > 8:
            return None
        if isinstance(value, str):
            return "[credential-like content omitted]" if contains_secret_material(value) else value[:3000]
        if isinstance(value, list):
            return [clean(item, depth + 1) for item in value[-12:]]
        if isinstance(value, dict):
            return {key: clean(item, depth + 1) for key, item in value.items()
                    if key.casefold() not in {"credentials", "credential", "password", "token", "config", "secret"}}
        return value if value is None or isinstance(value, (int, float, bool)) else str(value)

    return clean({key: context.get(key) for key in (
        "thread", "relevantAttachments", "standingDirectives", "activeJobs", "recentResults"
    )})
