"""Optional native integration hooks; absence always means unavailable.

These are separate from ExecutionProvider so existing browser/native contracts
remain compatible. Secrets are confined to authentication/execution hooks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.execution.contracts import (
    CapabilityDescriptor,
    ExecutionRequest,
    ExecutionResult,
    ResourceDescriptor,
)


class HookUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CredentialBundle:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()
    renewal_metadata: dict = field(default_factory=dict, repr=False)
    account_id: str | None = None


@dataclass(frozen=True)
class ResourcePage:
    resources: tuple[ResourceDescriptor, ...]
    next_cursor: str | None = None


@dataclass(frozen=True)
class VerifiedEvent:
    delivery_id: str
    account_id: str
    event_type: str
    resource_external_id: str
    evidence: dict
    external_object_id: str | None = None
    self_generated: bool = False


@runtime_checkable
class AuthenticationHooks(Protocol):
    async def initiate_authorization(self, *, state: str, callback_url: str) -> str: ...
    async def complete_authorization(self, *, code: str, callback_url: str) -> CredentialBundle: ...
    async def renew_credentials(self, *, bundle: CredentialBundle) -> CredentialBundle: ...
    async def revoke_credentials(self, *, bundle: CredentialBundle) -> None: ...


@runtime_checkable
class ConsentHooks(Protocol):
    def capabilities_for_granted_scopes(self, *, scopes: tuple[str, ...]) -> tuple[str, ...]: ...


@runtime_checkable
class CredentialRenewalHooks(Protocol):
    def can_renew_credentials(self, *, bundle: CredentialBundle) -> bool: ...


@runtime_checkable
class ScopedCredentialHooks(Protocol):
    async def scope_execution_credential(
        self, *, configuration: dict, scope: str, bundle: CredentialBundle, input: dict
    ) -> tuple[str, CredentialBundle]: ...


@runtime_checkable
class ExecutionEvidenceHooks(Protocol):
    async def record_dispatch(self, *, session, request: ExecutionRequest) -> None: ...
    async def record_outcome(self, *, session, request: ExecutionRequest, verification) -> None: ...


@runtime_checkable
class ActionPolicyHooks(Protocol):
    def action_capability(
        self, *, capability: CapabilityDescriptor, input: dict
    ) -> CapabilityDescriptor: ...


@runtime_checkable
class ResourceHooks(Protocol):
    async def discover_resource_page(
        self, *, configuration: dict[str, str], credential: str | None, cursor: str | None
    ) -> ResourcePage: ...
    async def lookup_resource(
        self, *, external_id: str, configuration: dict[str, str], credential: str | None
    ) -> ResourceDescriptor: ...


@runtime_checkable
class ReconciliationHooks(Protocol):
    async def reconcile_write(
        self, *, request: ExecutionRequest, configuration: dict[str, str], credential: str | None
    ) -> ExecutionResult | None: ...


@runtime_checkable
class EventHooks(Protocol):
    async def verify_and_normalize_event(
        self, *, headers: dict[str, str], body: bytes
    ) -> VerifiedEvent: ...


@runtime_checkable
class EventRoutingHooks(Protocol):
    async def accept_event(self, *, session, event: VerifiedEvent): ...
    async def event_allows_matching(self, *, session, event) -> bool: ...
