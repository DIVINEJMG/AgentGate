from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class HumanPrincipal:
    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    role: str
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    agent_id: UUID
    organization_id: UUID
    credential_fingerprint: str
    capabilities: frozenset[str]
    risk_level: str
    policy_context: tuple[tuple[str, str], ...]
