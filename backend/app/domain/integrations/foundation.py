"""Pure authority and checkpoint rules shared by every native adapter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class IntegrationNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    resource_hint: str = ""
    account_hint: str = ""
    scopes: list[str] = Field(min_length=1)
    # Constraints must be grounded in the human instruction, never provider text.
    destinations: dict[str, str] = Field(default_factory=dict)
    constraints: dict[str, list[str]] = Field(default_factory=dict)


class IntegrationTaskDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=2, max_length=6000)
    completion_criteria: list[str] = Field(min_length=1)
    needs: list[IntegrationNeed] = Field(min_length=1, max_length=12)
    standing_request_excerpt: str | None = Field(default=None, max_length=500)
    runtime_requirements: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class BoundResource:
    connection_id: str
    resource_id: str
    provider: str
    scopes: tuple[str, ...]
    destinations: dict[str, str]


def payload_fingerprint(resource: str, scope: str, payload: dict, authority_version: int) -> str:
    return hashlib.sha256(json.dumps([resource, scope, payload, authority_version], sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def grant_allows(*, allowed_scopes: list[str], destinations: dict, scope: str, payload: dict) -> bool:
    return scope in allowed_scopes and all(payload.get(key) == value for key, value in destinations.items())


def select_resource(candidates: list[dict], need: IntegrationNeed) -> dict | None:
    matches = [r for r in candidates if r["provider"] == need.provider
               and set(need.scopes).issubset(r["capabilities"])]
    if need.account_hint:
        matches = [r for r in matches if need.account_hint.casefold() in (r.get("account", "").casefold(), r["connectionId"].casefold())]
    if need.resource_hint:
        hint = need.resource_hint.casefold()
        matches = [r for r in matches if hint in (r["id"].casefold(), r["externalId"].casefold(), r["name"].casefold(),
            *(str(alias).casefold() for alias in r.get("aliases", [])))]
    # Never pick the first account or infer authority from search/provider text.
    return matches[0] if len(matches) == 1 else None


def recovery_disposition(*, side_effect: bool, error_code: str, retryable: bool, reconciled: bool = False) -> str:
    if error_code in {"authentication_error", "credential_missing"}:
        return "waiting_reconnect"
    if side_effect and error_code in {"timeout", "provider_unavailable", "temporary_provider_error", "verification_failed", "uncertain_outcome"}:
        return "verified" if reconciled else "uncertain_outcome"
    return "retrying" if retryable else "failed"
