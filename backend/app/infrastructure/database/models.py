from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class HumanIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "human_identities"
    subject: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(160))

class ExternalAuthIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "external_auth_identities"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_subject: Mapped[str] = mapped_column(String(64), nullable=False)
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_external_provider_subject"),
        UniqueConstraint("user_id", "provider", name="uq_external_user_provider"),
    )


class GitHubAuthFlow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "github_auth_flows"
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    browser_challenge: Mapped[str] = mapped_column(String(64), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(512))
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="authorizing", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("human_identities.id", ondelete="CASCADE"))
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    onboarding_id: Mapped[UUID | None] = mapped_column(ForeignKey("github_onboarding.id", ondelete="SET NULL"))
    __table_args__ = (
        CheckConstraint("purpose in ('signin','signup','link')", name="ck_github_auth_purpose"),
        Index("ix_github_auth_expiry", "expires_at"),
    )


class LocalAuthCredential(TimestampMixin, Base):
    __tablename__ = "local_auth_credentials"
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("human_identities.id", ondelete="CASCADE"),
        primary_key=True,
    )
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)
    __table_args__ = (CheckConstraint("length(name) >= 2", name="ck_organizations_name_length"),)

class OrganizationMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organization_memberships"
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        CheckConstraint("role in ('owner','admin','security_manager','operator','approver','viewer')", name="ck_membership_role"),
        Index("ix_memberships_user_org", "user_id", "organization_id"),
    )

class TenantModel(UUIDPrimaryKeyMixin, TimestampMixin):
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)


class GitHubInstallationBinding(TenantModel, Base):
    __tablename__ = "github_installation_bindings"
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), unique=True)
    installation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    verified_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    permissions: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    __table_args__ = (Index("ix_github_installation", "installation_id"),)


class GitHubOnboarding(TenantModel, Base):
    __tablename__ = "github_onboarding"
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)
    reconnect_connection_id: Mapped[UUID | None] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="authorizing", nullable=False)


class IntegrationResourceAlias(TenantModel, Base):
    __tablename__ = "integration_resource_aliases"
    resource_id: Mapped[UUID] = mapped_column(ForeignKey("integration_resources.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(512), nullable=False)
    __table_args__ = (UniqueConstraint("resource_id", "alias", name="uq_resource_alias"),)


class GitHubActionEvidence(TenantModel, Base):
    __tablename__ = "github_action_evidence"
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    action_key: Mapped[str] = mapped_column(String(255), nullable=False)
    operation: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint("connection_id", "action_key", name="uq_github_action_evidence"),)


class CodingSession(TenantModel, Base):
    __tablename__ = "coding_sessions"
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    resource_id: Mapped[UUID] = mapped_column(ForeignKey("integration_resources.id", ondelete="CASCADE"))
    sandbox_id: Mapped[str | None] = mapped_column(String(255))
    base_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="creating", nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    active_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    active_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint("work_item_id", "resource_id", name="uq_coding_workspace"),)


class CodingCommand(TenantModel, Base):
    __tablename__ = "coding_commands"
    session_id: Mapped[UUID] = mapped_column(ForeignKey("coding_sessions.id", ondelete="CASCADE"))
    action_key: Mapped[str] = mapped_column(String(255), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    process_id: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="starting", nullable=False)
    output: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    __table_args__ = (UniqueConstraint("session_id", "action_key", name="uq_coding_command_action"),)


class CodingBudget(TenantModel, Base):
    __tablename__ = "coding_budgets"
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    reserved_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("organization_id", "day", name="uq_coding_daily_budget"),)


class IntegrationConnectionState(TenantModel, Base):
    __tablename__ = "integration_connection_states"
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), unique=True, nullable=False)
    owner_id: Mapped[UUID | None] = mapped_column(ForeignKey("human_identities.id"))
    ownership_state: Mapped[str] = mapped_column(String(32), default="review_required", nullable=False)
    authorization_state: Mapped[str] = mapped_column(String(32), default="connected", nullable=False)
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    authority_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    credential_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    account_id: Mapped[str | None] = mapped_column(String(255))


class IntegrationResource(TenantModel, Base):
    __tablename__ = "integration_resources"
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(240), nullable=False)
    capabilities: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    health: Mapped[str] = mapped_column(String(32), default="healthy", nullable=False)
    configuration: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    web_url: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("connection_id", "external_id", "resource_type", name="uq_integration_resource_identity"),)


class IntegrationAccessGrant(TenantModel, Base):
    __tablename__ = "integration_access_grants"
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(nullable=False)
    granted_by: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("connection_id", "subject_type", "subject_id", name="uq_integration_access_subject"), CheckConstraint("subject_type in ('user','worker')", name="ck_integration_access_subject"))


class IntegrationTaskGrant(TenantModel, Base):
    __tablename__ = "integration_task_grants"
    job_revision_id: Mapped[UUID] = mapped_column(ForeignKey("job_revisions.id", ondelete="CASCADE"), nullable=False)
    work_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    worker_id: Mapped[UUID] = mapped_column(ForeignKey("workers.id", ondelete="CASCADE"), nullable=False)
    initiating_user_id: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(ForeignKey("integration_resources.id", ondelete="CASCADE"), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False)
    destinations: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    constraints: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    standing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    authority_version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("job_revision_id", "resource_id", name="uq_integration_task_binding"),)


class IntegrationEventInbox(TenantModel, Base):
    __tablename__ = "integration_event_inbox"
    connection_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    __table_args__ = (UniqueConstraint("provider", "connection_id", "delivery_id", name="uq_integration_event_delivery"),)


class IntegrationNotification(TenantModel, Base):
    __tablename__ = "integration_notifications"
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False)
    notification_key: Mapped[str] = mapped_column(String(255), nullable=False)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_messages.id", ondelete="CASCADE", deferrable=True, initially="DEFERRED"), nullable=False)
    __table_args__ = (UniqueConstraint("work_item_id", "notification_key", name="uq_integration_notification"),)


class IntegrationAuthState(TenantModel, Base):
    __tablename__ = "integration_auth_states"
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    callback_url: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class IntegrationEventSubscription(TenantModel, Base):
    __tablename__ = "integration_event_subscriptions"
    job_revision_id: Mapped[UUID] = mapped_column(ForeignKey("job_revisions.id", ondelete="CASCADE"), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(ForeignKey("integration_resources.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    results_thread_id: Mapped[UUID] = mapped_column(ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (UniqueConstraint("job_revision_id", "resource_id", "event_type", name="uq_integration_event_subscription"),)

class AgentIdentity(TenantModel, Base):
    __tablename__ = "agent_identities"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)
    __table_args__ = (Index("ix_agent_identities_org_status", "organization_id", "status"),)

class AgentCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_credentials"
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agent_identities.id", ondelete="CASCADE"), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

class WorkforceRole(TenantModel, Base):
    __tablename__ = "workforce_roles"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    defaults: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_workforce_role_name"),)

class Worker(TenantModel, Base):
    __tablename__ = "workers"
    agent_identity_id: Mapped[UUID] = mapped_column(ForeignKey("agent_identities.id"), nullable=False, unique=True)
    role_id: Mapped[UUID | None] = mapped_column(ForeignKey("workforce_roles.id"))
    supervisor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("human_identities.id"))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    department: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    profile: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

class Job(TenantModel, Base):
    __tablename__ = "jobs"
    worker_id: Mapped[UUID] = mapped_column(ForeignKey("workers.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __table_args__ = (Index("ix_jobs_org_worker_status", "organization_id", "worker_id", "status"),)

class JobRevision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_revisions"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("job_id", "revision", name="uq_job_revision"),)

class WorkItem(TenantModel, Base):
    __tablename__ = "work_items"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    job_revision_id: Mapped[UUID] = mapped_column(ForeignKey("job_revisions.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_work_item_idempotency"),
        Index("ix_work_items_queue", "organization_id", "status", "scheduled_at"),
    )

class Run(TenantModel, Base):
    __tablename__ = "runs"
    work_item_id: Mapped[UUID] = mapped_column(ForeignKey("work_items.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result_summary: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_runs_org_status", "organization_id", "status"),)

class RunStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "run_steps"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("run_id", "step_index", name="uq_run_step_index"),)

class Integration(TenantModel, Base):
    __tablename__ = "integrations"
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

class IntegrationCredential(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "integration_credentials"
    integration_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False, unique=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

class CapabilityProfile(TenantModel, Base):
    __tablename__ = "capability_profiles"
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agent_identities.id", ondelete="CASCADE"), nullable=False)
    scope: Mapped[str] = mapped_column(String(180), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    __table_args__ = (UniqueConstraint("agent_id", "scope", name="uq_agent_capability_scope"),)

class Policy(TenantModel, Base):
    __tablename__ = "policies"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

class PolicyRevision(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "policy_revisions"
    policy_id: Mapped[UUID] = mapped_column(ForeignKey("policies.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    effect: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    selectors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("policy_id", "revision", name="uq_policy_revision"),
        CheckConstraint("priority between 0 and 1000", name="ck_policy_priority"),
        CheckConstraint("effect in ('allow','deny','require_approval')", name="ck_policy_effect"),
    )

class Action(TenantModel, Base):
    __tablename__ = "actions"
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agent_identities.id"), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id"))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(180), nullable=False)
    scope: Mapped[str] = mapped_column(String(180), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (UniqueConstraint("organization_id", "idempotency_key", name="uq_action_idempotency"),)

class Approval(TenantModel, Base):
    __tablename__ = "approvals"
    action_id: Mapped[UUID] = mapped_column(ForeignKey("actions.id"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("human_identities.id"))
    decision_reason: Mapped[str | None] = mapped_column(Text)

class RiskEvent(TenantModel, Base):
    __tablename__ = "risk_events"
    agent_id: Mapped[UUID] = mapped_column(ForeignKey("agent_identities.id"), nullable=False)
    effective_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    factors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

class Incident(TenantModel, Base):
    __tablename__ = "incidents"
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

class ExecutionControl(TenantModel, Base):
    __tablename__ = "execution_controls"
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    suspended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("organization_id", "target_type", "target_id", name="uq_execution_control_target"),)

class Memory(TenantModel, Base):
    __tablename__ = "memories"
    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(32), nullable=False, default="operational")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="human")
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sensitivity: Mapped[str] = mapped_column(String(24), nullable=False, default="internal")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_memories_org_owner_type", "organization_id", "owner_id", "memory_type"),
    )

class Artifact(TenantModel, Base):
    __tablename__ = "artifacts"
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id"))
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

class ArtifactAnalysis(TenantModel, Base):
    __tablename__ = "artifact_analyses"
    artifact_id: Mapped[UUID] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    analyzer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    findings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sensitivity: Mapped[str] = mapped_column(String(24), nullable=False, default="internal")
    __table_args__ = (
        Index("ix_artifact_analyses_org_created", "organization_id", "created_at"),
    )

class Result(TenantModel, Base):
    __tablename__ = "results"
    worker_id: Mapped[UUID] = mapped_column(ForeignKey("workers.id"), nullable=False)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)

class ResultVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "result_versions"
    result_id: Mapped[UUID] = mapped_column(ForeignKey("results.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("result_id", "version", name="uq_result_version"),)

class ResultExport(TenantModel, Base):
    __tablename__ = "result_exports"
    result_id: Mapped[UUID] = mapped_column(ForeignKey("results.id"), nullable=False)
    format: Mapped[str] = mapped_column(String(24), nullable=False)
    exported_by: Mapped[UUID] = mapped_column(ForeignKey("human_identities.id"), nullable=False)

class AuditEvent(TenantModel, Base):
    __tablename__ = "audit_events"
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    actor: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resource: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (Index("ix_audit_org_created", "organization_id", "created_at"),)

class OutboxEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outbox_events"
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_delivery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "created_at"),)




class ConversationThread(TenantModel, Base):
    __tablename__ = "conversation_threads"
    worker_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="New conversation")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("human_identities.id"), nullable=False
    )
    __table_args__ = (
        Index("ix_conversation_threads_org_updated", "organization_id", "updated_at"),
        Index("ix_conversation_threads_worker_updated", "worker_id", "updated_at"),
    )


class ConversationMessage(TenantModel, Base):
    __tablename__ = "conversation_messages"
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(24), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_references: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    command_references: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    result_references: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    __table_args__ = (
        CheckConstraint(
            "role in ('human','worker','system')",
            name="ck_conversation_message_role",
        ),
        Index("ix_conversation_messages_thread_created", "thread_id", "created_at"),
        Index("ix_conversation_messages_org_created", "organization_id", "created_at"),
    )


class ConversationCommand(TenantModel, Base):
    __tablename__ = "conversation_commands"
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="CASCADE"), nullable=False
    )
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    receipt: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("human_identities.id"), nullable=False
    )
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    __table_args__ = (
        Index("ix_conversation_commands_org_created", "organization_id", "created_at"),
        Index("ix_conversation_commands_thread_created", "thread_id", "created_at"),
    )


class WorkerDirective(TenantModel, Base):
    __tablename__ = "worker_directives"
    worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("workers.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("human_identities.id"), nullable=False
    )
    source_thread_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="SET NULL")
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL")
    )
    __table_args__ = (
        Index("ix_worker_directives_org_worker", "organization_id", "worker_id", "status"),
    )



class AIInvocation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    transport_identity: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    __tablename__ = "ai_invocations"
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    worker_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workers.id", ondelete="SET NULL")
    )
    job_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL")
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversation_threads.id", ondelete="SET NULL")
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_id: Mapped[str | None] = mapped_column(String(255))
    usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    error_category: Mapped[str | None] = mapped_column(String(64))
    schema_name: Mapped[str | None] = mapped_column(String(128))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    __table_args__ = (
        Index("ix_ai_invocations_org_created", "organization_id", "created_at"),
        Index("ix_ai_invocations_role_created", "role", "created_at"),
    )

class QueueDeliveryFailure(TenantModel, Base):
    __tablename__ = "queue_delivery_failures"
    source_message_id: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    dlq_id: Mapped[str | None] = mapped_column(String(180))
    status_code: Mapped[int | None] = mapped_column(Integer)
    retried: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    destination_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    failure_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index(
            "ix_queue_delivery_failures_org_created",
            "organization_id",
            "created_at",
        ),
    )


class PlannerDecision(TenantModel, Base):
    __tablename__ = "planner_decisions"
    work_item_id: Mapped[UUID | None] = mapped_column(ForeignKey("work_items.id", ondelete="CASCADE"))
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    command_id: Mapped[UUID | None] = mapped_column(ForeignKey("conversation_commands.id", ondelete="CASCADE"))
    preparation_revision_id: Mapped[UUID | None] = mapped_column(ForeignKey("job_revisions.id", ondelete="CASCADE"))
    workload: Mapped[str] = mapped_column(String(32), default="complex", server_default="complex")
    purpose: Mapped[str] = mapped_column(String(64), default="execution_planning", server_default="execution_planning")
    step_index: Mapped[int] = mapped_column(Integer)
    context_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    routes: Mapped[list] = mapped_column(JSONB, default=list)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    recovery_round: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_provider: Mapped[str | None] = mapped_column(String(64))
    accepted_model: Mapped[str | None] = mapped_column(String(255))
    accepted_proposal: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("work_item_id", "run_id", "step_index", name="uq_planner_pending_decision"),
        UniqueConstraint("organization_id", "command_id", "purpose", "step_index", name="uq_preparation_decision"),
        UniqueConstraint("organization_id", "preparation_revision_id", "purpose", "step_index", name="uq_worker_preparation_decision"),
        CheckConstraint("(command_id IS NULL AND preparation_revision_id IS NULL AND work_item_id IS NOT NULL AND run_id IS NOT NULL) OR "
                        "(command_id IS NOT NULL AND preparation_revision_id IS NULL AND work_item_id IS NULL AND run_id IS NULL) OR "
                        "(preparation_revision_id IS NOT NULL AND command_id IS NULL AND work_item_id IS NULL AND run_id IS NULL)",
                        name="ck_planner_decision_target"),
    )


class PlannerAttempt(TenantModel, Base):
    failure_details: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    transport_identity: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    __tablename__ = "planner_attempts"
    decision_id: Mapped[UUID] = mapped_column(ForeignKey("planner_decisions.id", ondelete="CASCADE"))
    route_index: Mapped[int] = mapped_column(Integer)
    recovery_round: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="started")
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    transport_success: Mapped[bool] = mapped_column(Boolean, default=False)
    schema_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(64))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("decision_id", "recovery_round", "route_index", name="uq_planner_route_attempt"),)
