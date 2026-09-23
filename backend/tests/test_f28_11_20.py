import logging
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.migration_order import assert_migration_order
from app.domain.identity.principals import AgentPrincipal, HumanPrincipal
from app.domain.integrations.contracts import (
    IntegrationManifest,
    IntegrationOperation,
    provider_manifest,
)
from app.domain.secrets.vault import require_secret_reference
from app.domain.security.invariants import (
    require_execution_allowed,
    require_human_approver,
    require_job_authority,
)
from app.observability.context import (
    ExecutionContext,
    bind_context,
    reset_context,
    structured_event,
)


def test_integration_manifest_describes_provider_operations() -> None:
    operation_manifest = IntegrationManifest(
        provider="github",
        resource="repository",
        capability="github.repository.issues.create",
        action="issue.create",
        scope="github.repository.issues.create",
        risk="medium",
        input_schema={"title": {"type": "string"}},
        output_schema={"id": {"type": "string"}},
        side_effect=True,
        approval_default=True,
        credential_strategy="oauth2",
    )

    class Adapter:
        provider = "github"
        manifests = (operation_manifest,)

        async def execute(self, **kwargs):
            raise NotImplementedError

    manifest = provider_manifest(Adapter())
    operation: IntegrationOperation = manifest.operations[0]
    assert manifest.provider == "github"
    assert manifest.resources == ("repository",)
    assert manifest.capabilities == ("github.repository.issues.create",)
    assert operation.action == "issue.create"
    assert operation.side_effect == "write"


def test_domain_migration_order_rejects_big_bang_skips() -> None:
    with pytest.raises(ValueError, match="runtime"):
        assert_migration_order({"runtime"})


def test_provider_credentials_require_secret_references() -> None:
    assert require_secret_reference("secret://github-prod").id == "github-prod"
    with pytest.raises(ValueError):
        require_secret_reference("ghp_plaintext")


def test_agent_cannot_approve_action() -> None:
    agent = AgentPrincipal(
        agent_id=uuid4(),
        organization_id=uuid4(),
        credential_fingerprint="sha256:test",
        capabilities=frozenset(),
        risk_level="low",
        policy_context=(),
    )
    with pytest.raises(PermissionError, match="Agent cannot approve"):
        require_human_approver(agent)


def test_human_can_be_approval_actor() -> None:
    human = HumanPrincipal(
        user_id=uuid4(),
        organization_id=uuid4(),
        membership_id=uuid4(),
        role="approver",
        permissions=frozenset({"approvals.decide"}),
    )
    assert require_human_approver(human) is human


def test_incident_kill_switch_overrides_execution() -> None:
    with pytest.raises(PermissionError, match="kill switch"):
        require_execution_allowed(
            incident_suspended=True,
            resumed=False,
            security_revalidated=False,
        )


def test_resumed_work_revalidates_security() -> None:
    with pytest.raises(PermissionError, match="revalidate"):
        require_execution_allowed(
            incident_suspended=False,
            resumed=True,
            security_revalidated=False,
        )
    require_execution_allowed(
        incident_suspended=False,
        resumed=True,
        security_revalidated=True,
    )


def test_job_requirement_does_not_grant_capability() -> None:
    with pytest.raises(PermissionError, match="does not independently authorize"):
        require_job_authority(capability_granted=False, job_requires_capability=True)


def test_observability_context_emits_correlation_dimensions(caplog) -> None:
    token = bind_context(
        ExecutionContext(
            organization_id="org-1",
            worker_id="worker-1",
            job_id="job-1",
            work_item_id="work-1",
            run_id="run-1",
            agent_id="agent-1",
            action_id="action-1",
            correlation_id="corr-1",
        )
    )
    try:
        with caplog.at_level(logging.INFO):
            structured_event(logging.getLogger("test"), "run.started")
    finally:
        reset_context(token)
    assert "corr-1" in caplog.text
    assert "action-1" in caplog.text


def test_typescript_reference_tree_remains_available() -> None:
    root = Path(__file__).resolve().parents[2]
    assert (root / "reference" / "typescript-backend").is_dir()
