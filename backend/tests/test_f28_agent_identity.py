import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workforce_routes import automatic_agent, create_worker
from app.application.services.agent_identity import AgentIdentityService
from app.domain.identity.agents import (
    AgentCredentialView,
    AgentDomainError,
    AgentIdentityView,
)
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.database.agent_repository import SQLAlchemyAgentRepository
from app.infrastructure.database.models import AgentCredential, AgentIdentity


class FakeAgentRepository:
    def __init__(self, role: str | None = "owner") -> None:
        self.role = role
        self.agents: dict[UUID, AgentIdentityView] = {}

    async def membership_role(self, user_id, organization_id):
        return self.role

    async def list_agents(self, organization_id):
        return [
            agent
            for agent in self.agents.values()
            if agent.organization_id == organization_id
        ]

    async def get_agent(self, organization_id, agent_id):
        agent = self.agents.get(agent_id)
        if agent is None or agent.organization_id != organization_id:
            return None
        return agent

    async def create_agent(
        self,
        *,
        user_id,
        organization_id,
        name,
        description,
        credential_hash,
        credential_fingerprint,
        scopes,
        created_at,
    ):
        assert credential_hash
        agent = AgentIdentityView(
            id=uuid4(),
            organization_id=organization_id,
            name=name,
            description=description,
            owner_user_id=user_id,
            status="active",
            credential=AgentCredentialView(
                status="active",
                fingerprint=credential_fingerprint,
                version=1,
                scopes=scopes,
                expires_at=None,
                last_used_at=None,
            ),
            created_at=created_at,
            updated_at=created_at,
        )
        self.agents[agent.id] = agent
        return agent

    async def set_status(
        self,
        *,
        user_id,
        organization_id,
        agent_id,
        status,
        changed_at,
    ):
        current = self.agents[agent_id]
        credential = current.credential
        if status == "disabled":
            credential = AgentCredentialView(
                status="revoked",
                fingerprint=credential.fingerprint,
                version=credential.version,
                scopes=credential.scopes,
                expires_at=credential.expires_at,
                last_used_at=credential.last_used_at,
            )
        updated = AgentIdentityView(
            id=current.id,
            organization_id=current.organization_id,
            name=current.name,
            description=current.description,
            owner_user_id=current.owner_user_id,
            status=status,
            credential=credential,
            created_at=current.created_at,
            updated_at=changed_at,
        )
        self.agents[agent_id] = updated
        return updated

    async def rotate_credential(
        self,
        *,
        user_id,
        organization_id,
        agent_id,
        credential_hash,
        credential_fingerprint,
        scopes,
        version,
        created_at,
    ):
        current = self.agents[agent_id]
        updated = AgentIdentityView(
            id=current.id,
            organization_id=current.organization_id,
            name=current.name,
            description=current.description,
            owner_user_id=current.owner_user_id,
            status=current.status,
            credential=AgentCredentialView(
                status="active",
                fingerprint=credential_fingerprint,
                version=version,
                scopes=scopes,
                expires_at=None,
                last_used_at=None,
            ),
            created_at=current.created_at,
            updated_at=created_at,
        )
        self.agents[agent_id] = updated
        return updated

    async def revoke_credential(
        self,
        *,
        user_id,
        organization_id,
        agent_id,
        changed_at,
    ):
        current = self.agents[agent_id]
        updated = AgentIdentityView(
            id=current.id,
            organization_id=current.organization_id,
            name=current.name,
            description=current.description,
            owner_user_id=current.owner_user_id,
            status="disabled" if current.status == "disabled" else "suspended",
            credential=AgentCredentialView(
                status="revoked",
                fingerprint=current.credential.fingerprint,
                version=current.credential.version,
                scopes=current.credential.scopes,
                expires_at=current.credential.expires_at,
                last_used_at=current.credential.last_used_at,
            ),
            created_at=current.created_at,
            updated_at=changed_at,
        )
        self.agents[agent_id] = updated
        return updated


@pytest.mark.asyncio
async def test_agent_registration_matches_reference_lifecycle() -> None:
    repository = FakeAgentRepository()
    service = AgentIdentityService(repository)
    user_id, organization_id = uuid4(), uuid4()

    agent, credential = await service.register_agent(
        user_id, organization_id, "Release Worker", "Deploys approved releases."
    )

    assert agent.status == "active"
    assert agent.owner_user_id == user_id
    assert agent.credential.status == "active"
    assert credential.secret.startswith("agt_sk_")
    assert credential.fingerprint.startswith("sha256:")
    assert credential.scopes == ("agent.authenticate",)


@pytest.mark.asyncio
async def test_revoking_credential_suspends_agent() -> None:
    repository = FakeAgentRepository()
    service = AgentIdentityService(repository)
    user_id, organization_id = uuid4(), uuid4()
    agent, _ = await service.register_agent(user_id, organization_id, "Worker", "")

    revoked = await service.revoke_credential(user_id, organization_id, agent.id)

    assert revoked.status == "suspended"
    assert revoked.credential.status == "revoked"


@pytest.mark.asyncio
async def test_disabled_agent_cannot_be_reactivated_or_rotated() -> None:
    repository = FakeAgentRepository()
    service = AgentIdentityService(repository)
    user_id, organization_id = uuid4(), uuid4()
    agent, _ = await service.register_agent(user_id, organization_id, "Worker", "")
    disabled = await service.set_lifecycle(
        user_id, organization_id, agent.id, "disabled"
    )

    assert disabled.credential.status == "revoked"
    with pytest.raises(AgentDomainError, match="cannot be reactivated"):
        await service.set_lifecycle(user_id, organization_id, agent.id, "active")
    with pytest.raises(AgentDomainError, match="cannot receive new credentials"):
        await service.rotate_credential(user_id, organization_id, agent.id)


@pytest.mark.asyncio
async def test_agent_permission_and_tenant_access_fail_closed() -> None:
    organization_id = uuid4()
    service = AgentIdentityService(FakeAgentRepository(role="viewer"))
    with pytest.raises(AgentDomainError) as error:
        await service.register_agent(uuid4(), organization_id, "Worker", "")
    assert error.value.status_code == 403

    missing = AgentIdentityService(FakeAgentRepository(role=None))
    with pytest.raises(AgentDomainError) as error:
        await missing.list_agents(uuid4(), organization_id)
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_disabled_legacy_identity_without_credential_remains_listable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = SQLAlchemyAgentRepository(AsyncMock())
    monkeypatch.setattr(repository, "_latest_credential", AsyncMock(return_value=None))
    now = datetime.now(UTC)
    agent = SimpleNamespace(
        id=uuid4(), organization_id=uuid4(), created_by=uuid4(),
        name="Legacy worker", status="disabled", created_at=now, updated_at=now,
    )

    view = await repository._view(cast(AgentIdentity, agent))

    assert view.status == "disabled"
    assert view.credential.status == "missing"
    assert view.credential.scopes == ()
    assert view.credential.fingerprint == ""


@pytest.mark.asyncio
async def test_automatic_agent_issues_an_active_hashed_credential() -> None:
    class CaptureSession:
        def __init__(self) -> None:
            self.added: list[object] = []
            self.committed = False

        def add(self, row: object) -> None:
            self.added.append(row)

        async def flush(self) -> None:
            for row in self.added:
                if isinstance(row, AgentIdentity) and row.id is None:
                    row.id = uuid4()

        async def commit(self) -> None:
            self.committed = True

    session = CaptureSession()
    organization_id = uuid4()
    principal = HumanPrincipal(
        user_id=uuid4(), organization_id=organization_id,
        membership_id=uuid4(), role="owner", permissions=frozenset(),
    )

    agent, reveal = await automatic_agent(cast(AsyncSession, session), organization_id, principal, "Research worker")

    credentials = [row for row in session.added if isinstance(row, AgentCredential)]
    assert len(credentials) == 1
    assert session.committed
    assert credentials[0].agent_id == agent.id
    assert credentials[0].status == "active"
    assert credentials[0].secret_hash == hashlib.sha256(reveal["secret"].encode()).hexdigest()
    assert reveal["secret"] not in credentials[0].secret_hash


@pytest.mark.asyncio
async def test_automatic_worker_cannot_bind_an_identity_without_active_credential() -> None:
    organization_id, agent_id = uuid4(), uuid4()
    principal = HumanPrincipal(
        user_id=uuid4(), organization_id=organization_id,
        membership_id=uuid4(), role="owner",
        permissions=frozenset({"workforce.manage"}),
    )
    session = AsyncMock()
    session.scalar.side_effect = [SimpleNamespace(id=agent_id, name="Research worker"), None]

    with pytest.raises(HTTPException, match="requires an active Agent credential"):
        await create_worker(
            session, organization_id, principal,
            {"agentIdentityId": str(agent_id), "name": "Research worker"},
            provisioning="automatic",
        )
