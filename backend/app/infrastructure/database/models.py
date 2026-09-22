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
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class Artifact(TenantModel, Base):
    __tablename__ = "artifacts"
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id"))
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)

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
    __table_args__ = (Index("ix_outbox_unpublished", "published_at", "created_at"),)


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
