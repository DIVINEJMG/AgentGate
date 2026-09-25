from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import (
    Action,
    Approval,
    Artifact,
    ArtifactAnalysis,
    CapabilityProfile,
    ConversationMessage,
    ConversationThread,
    HumanIdentity,
    Integration,
    Job,
    JobRevision,
    Memory,
    Policy,
    PolicyRevision,
    Result,
    ResultVersion,
    Run,
    RunStep,
    Worker,
    WorkerDirective,
    WorkforceRole,
    WorkItem,
)

MAX_WORKERS = 25
MAX_JOBS = 30
MAX_POLICIES = 30
MAX_INTEGRATIONS = 30
MAX_CAPABILITIES = 80
MAX_WORK_ITEMS = 30
MAX_RUNS = 20
MAX_RESULTS = 15
MAX_MEMORIES = 12
MAX_MESSAGES = 30
MAX_ARTIFACTS = 12
MAX_DIRECTIVES = 20
MAX_PENDING_APPROVALS = 20


def _clip(value: object, limit: int = 1200) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _ids(values: Iterable[UUID]) -> list[UUID]:
    return list(dict.fromkeys(values))


class ConversationContextAssembler:
    """Build bounded model context strictly from canonical organization state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def assemble(
        self,
        *,
        organization_id: UUID,
        thread: ConversationThread,
    ) -> dict[str, Any]:
        scoped_worker = None
        if thread.worker_id is not None:
            scoped_worker = await self._session.scalar(
                select(Worker).where(
                    Worker.organization_id == organization_id,
                    Worker.id == thread.worker_id,
                )
            )

        workers = await self._workers(organization_id, scoped_worker)
        jobs = await self._jobs(organization_id, scoped_worker)
        job_ids = [job.id for job in jobs]
        work_items = await self._work_items(organization_id, job_ids, scoped_worker)
        runs = await self._runs(organization_id, [item.id for item in work_items])
        results = await self._results(organization_id, scoped_worker)
        messages = await self._messages(organization_id, thread.id)
        artifact_ids = self._artifact_ids(messages)

        return {
            "scope": {
                "organizationId": str(organization_id),
                "threadId": str(thread.id),
                "workerId": str(thread.worker_id) if thread.worker_id else None,
            },
            "worker": await self._worker_context(scoped_worker),
            "workers": [self._worker_summary(worker) for worker in workers],
            "activeJobs": [await self._job_context(job) for job in jobs],
            "standingDirectives": await self._directives(organization_id, scoped_worker),
            "activeSchedules": [
                schedule
                for job in jobs
                if (schedule := await self._schedule_context(job)) is not None
            ],
            "connectedIntegrations": await self._integrations(organization_id),
            "allowedCapabilities": await self._capabilities(organization_id, scoped_worker),
            "policiesAndApprovalBoundaries": await self._policies(organization_id),
            "pendingApprovals": await self._pending_approvals(organization_id, scoped_worker),
            "currentWork": [self._work_item_context(item) for item in work_items],
            "recentRuns": [await self._run_context(run) for run in runs],
            "recentResults": [await self._result_context(result) for result in results],
            "relevantMemories": await self._memories(organization_id, scoped_worker),
            "thread": {
                "id": str(thread.id),
                "title": thread.title,
                "status": thread.status,
                "messages": [self._message_context(message) for message in messages],
            },
            "relevantAttachments": await self._artifacts(organization_id, artifact_ids),
            "bounds": {
                "workers": MAX_WORKERS,
                "jobs": MAX_JOBS,
                "workItems": MAX_WORK_ITEMS,
                "runs": MAX_RUNS,
                "results": MAX_RESULTS,
                "messages": MAX_MESSAGES,
                "memories": MAX_MEMORIES,
                "attachments": MAX_ARTIFACTS,
            },
        }

    async def _workers(
        self,
        organization_id: UUID,
        scoped_worker: Worker | None,
    ) -> list[Worker]:
        if scoped_worker is not None:
            return [scoped_worker]
        rows = await self._session.scalars(
            select(Worker)
            .where(Worker.organization_id == organization_id)
            .order_by(desc(Worker.updated_at))
            .limit(MAX_WORKERS)
        )
        return list(rows.all())

    async def _jobs(
        self,
        organization_id: UUID,
        scoped_worker: Worker | None,
    ) -> list[Job]:
        statement = (
            select(Job)
            .where(
                Job.organization_id == organization_id,
                Job.status != "archived",
            )
            .order_by(desc(Job.updated_at))
            .limit(MAX_JOBS)
        )
        if scoped_worker is not None:
            statement = statement.where(Job.worker_id == scoped_worker.id)
        rows = await self._session.scalars(statement)
        return list(rows.all())

    async def _work_items(
        self,
        organization_id: UUID,
        job_ids: list[UUID],
        scoped_worker: Worker | None,
    ) -> list[WorkItem]:
        if scoped_worker is not None and not job_ids:
            return []
        statement = (
            select(WorkItem)
            .where(WorkItem.organization_id == organization_id)
            .order_by(desc(WorkItem.created_at))
            .limit(MAX_WORK_ITEMS)
        )
        if scoped_worker is not None:
            statement = statement.where(WorkItem.job_id.in_(job_ids))
        rows = await self._session.scalars(statement)
        return list(rows.all())

    async def _runs(
        self,
        organization_id: UUID,
        work_item_ids: list[UUID],
    ) -> list[Run]:
        if not work_item_ids:
            return []
        rows = await self._session.scalars(
            select(Run)
            .where(
                Run.organization_id == organization_id,
                Run.work_item_id.in_(work_item_ids),
            )
            .order_by(desc(Run.created_at))
            .limit(MAX_RUNS)
        )
        return list(rows.all())

    async def _results(
        self,
        organization_id: UUID,
        scoped_worker: Worker | None,
    ) -> list[Result]:
        statement = (
            select(Result)
            .where(Result.organization_id == organization_id)
            .order_by(desc(Result.created_at))
            .limit(MAX_RESULTS)
        )
        if scoped_worker is not None:
            statement = statement.where(Result.worker_id == scoped_worker.id)
        rows = await self._session.scalars(statement)
        return list(rows.all())

    async def _messages(
        self,
        organization_id: UUID,
        thread_id: UUID,
    ) -> list[ConversationMessage]:
        rows = list(
            (
                await self._session.scalars(
                    select(ConversationMessage)
                    .where(
                        ConversationMessage.organization_id == organization_id,
                        ConversationMessage.thread_id == thread_id,
                    )
                    .order_by(desc(ConversationMessage.created_at))
                    .limit(MAX_MESSAGES)
                )
            ).all()
        )
        rows.reverse()
        return rows

    async def _worker_context(self, worker: Worker | None) -> dict[str, Any] | None:
        if worker is None:
            return None
        role_name = None
        if worker.role_id is not None:
            role = await self._session.get(WorkforceRole, worker.role_id)
            if role is not None:
                role_name = role.name
        supervisor = None
        if worker.supervisor_user_id is not None:
            human = await self._session.get(HumanIdentity, worker.supervisor_user_id)
            if human is not None:
                supervisor = human.display_name or str(human.id)
        profile = dict(worker.profile or {})
        return {
            "id": str(worker.id),
            "name": worker.name,
            "status": worker.status,
            "role": role_name,
            "department": worker.department,
            "supervisor": supervisor,
            "charter": _clip(profile.get("description"), 1800),
            "responsibilities": [
                _clip(item, 500) for item in list(profile.get("responsibilities", []))[:20]
            ],
            "instructions": _clip(profile.get("instructions"), 1800),
        }

    def _worker_summary(self, worker: Worker) -> dict[str, Any]:
        return {
            "id": str(worker.id),
            "name": worker.name,
            "department": worker.department,
            "status": worker.status,
        }

    async def _revision(self, job: Job) -> JobRevision | None:
        return await self._session.scalar(
            select(JobRevision).where(
                JobRevision.job_id == job.id,
                JobRevision.revision == job.current_revision,
            )
        )

    async def _job_context(self, job: Job) -> dict[str, Any]:
        revision = await self._revision(job)
        definition = dict(revision.definition or {}) if revision is not None else {}
        return {
            "id": str(job.id),
            "workerId": str(job.worker_id),
            "name": job.name,
            "status": job.status,
            "objective": _clip(definition.get("objective"), 1400),
            "instructions": _clip(definition.get("instructions"), 1800),
            "completionCriteria": [
                _clip(item, 500) for item in list(definition.get("completionCriteria", []))[:15]
            ],
            "requiredCapabilities": [
                str(item) for item in list(definition.get("requiredCapabilities", []))[:40]
            ],
            "integrationRequirements": [
                str(item) for item in list(definition.get("integrationRequirements", []))[:20]
            ],
            "autonomy": (
                {
                    key: value
                    for key, value in dict(definition.get("autonomy") or {}).items()
                    if key
                    in {
                        "createdByAI",
                        "humanSchedule",
                        "startWhenReady",
                        "stopAfterNextRun",
                        "missingIntegrations",
                    }
                }
                if isinstance(definition.get("autonomy"), dict)
                else {}
            ),
        }

    async def _schedule_context(self, job: Job) -> dict[str, Any] | None:
        revision = await self._revision(job)
        if revision is None:
            return None
        definition = dict(revision.definition or {})
        raw = definition.get("triggerConfig")
        if not isinstance(raw, dict):
            return None
        schedule = raw.get("schedule")
        if not isinstance(schedule, dict):
            return None
        return {
            "id": str(raw.get("id") or ""),
            "jobId": str(job.id),
            "jobName": job.name,
            "enabled": bool(schedule.get("enabled")),
            "cadence": str(schedule.get("cadence") or ""),
            "timezone": str(schedule.get("timezone") or "UTC"),
            "localTime": str(schedule.get("localTime") or ""),
            "weekdays": list(schedule.get("weekdays", []))[:7],
            "intervalMinutes": schedule.get("intervalMinutes"),
            "nextDueAt": schedule.get("nextDueAt"),
        }

    async def _directives(
        self,
        organization_id: UUID,
        worker: Worker | None,
    ) -> list[dict[str, Any]]:
        if worker is None:
            return []
        rows = await self._session.scalars(
            select(WorkerDirective)
            .where(
                WorkerDirective.organization_id == organization_id,
                WorkerDirective.worker_id == worker.id,
                WorkerDirective.status == "active",
            )
            .order_by(desc(WorkerDirective.created_at))
            .limit(MAX_DIRECTIVES)
        )
        return [{"id": str(item.id), "text": _clip(item.text, 1200)} for item in rows.all()]

    async def _integrations(self, organization_id: UUID) -> list[dict[str, Any]]:
        rows = await self._session.scalars(
            select(Integration)
            .where(Integration.organization_id == organization_id)
            .order_by(Integration.provider)
            .limit(MAX_INTEGRATIONS)
        )
        return [
            {
                "id": str(item.id),
                "provider": item.provider,
                "displayName": item.display_name,
                "status": item.status,
            }
            for item in rows.all()
        ]

    async def _capabilities(
        self,
        organization_id: UUID,
        worker: Worker | None,
    ) -> list[dict[str, Any]]:
        if worker is None:
            return []
        rows = await self._session.scalars(
            select(CapabilityProfile)
            .where(
                CapabilityProfile.organization_id == organization_id,
                CapabilityProfile.agent_id == worker.agent_identity_id,
                CapabilityProfile.active.is_(True),
            )
            .order_by(CapabilityProfile.scope)
            .limit(MAX_CAPABILITIES)
        )
        return [{"scope": item.scope} for item in rows.all()]

    async def _policies(self, organization_id: UUID) -> list[dict[str, Any]]:
        policies = list(
            (
                await self._session.scalars(
                    select(Policy)
                    .where(
                        Policy.organization_id == organization_id,
                        Policy.status == "enabled",
                    )
                    .order_by(desc(Policy.updated_at))
                    .limit(MAX_POLICIES)
                )
            ).all()
        )
        result: list[dict[str, Any]] = []
        for policy in policies:
            revision = await self._session.scalar(
                select(PolicyRevision).where(
                    PolicyRevision.policy_id == policy.id,
                    PolicyRevision.revision == policy.current_revision,
                )
            )
            if revision is None:
                continue
            selectors = dict(revision.selectors or {})
            meta = selectors.pop("_meta", None)
            description = _clip(meta.get("description"), 600) if isinstance(meta, dict) else ""
            result.append(
                {
                    "id": str(policy.id),
                    "name": policy.name,
                    "effect": revision.effect,
                    "priority": revision.priority,
                    "selectors": selectors,
                    "description": description,
                }
            )
        return result

    async def _pending_approvals(
        self,
        organization_id: UUID,
        worker: Worker | None,
    ) -> list[dict[str, Any]]:
        statement = (
            select(Approval, Action)
            .join(Action, Approval.action_id == Action.id)
            .where(
                Approval.organization_id == organization_id,
                Approval.status == "pending",
                Action.organization_id == organization_id,
            )
            .order_by(desc(Approval.created_at))
            .limit(MAX_PENDING_APPROVALS)
        )
        if worker is not None:
            statement = statement.where(Action.agent_id == worker.agent_identity_id)
        rows = (await self._session.execute(statement)).all()
        return [
            {
                "approvalId": str(approval.id),
                "actionId": str(action.id),
                "scope": action.scope,
                "resourceId": action.resource_id,
                "runId": str(action.run_id) if action.run_id else None,
            }
            for approval, action in rows
        ]

    def _work_item_context(self, item: WorkItem) -> dict[str, Any]:
        return {
            "id": str(item.id),
            "jobId": str(item.job_id),
            "status": item.status,
            "scheduledAt": item.scheduled_at.isoformat(),
            "correlationId": item.correlation_id,
        }

    async def _run_context(self, run: Run) -> dict[str, Any]:
        steps = list(
            (
                await self._session.scalars(
                    select(RunStep)
                    .where(RunStep.run_id == run.id)
                    .order_by(RunStep.step_index)
                    .limit(12)
                )
            ).all()
        )
        evidence: list[dict[str, Any]] = []
        for step in steps:
            output = dict(step.output or {})
            evidence.append(
                {
                    "index": step.step_index,
                    "kind": step.kind,
                    "status": step.status,
                    "summary": _clip(
                        output.get("summary") or output.get("output") or output.get("error"),
                        700,
                    ),
                }
            )
        return {
            "id": str(run.id),
            "workItemId": str(run.work_item_id),
            "status": run.status,
            "summary": _clip(run.result_summary, 1200),
            "createdAt": run.created_at.isoformat(),
            "steps": evidence,
        }

    async def _result_context(self, result: Result) -> dict[str, Any]:
        version = await self._session.scalar(
            select(ResultVersion).where(
                ResultVersion.result_id == result.id,
                ResultVersion.version == result.latest_version,
            )
        )
        body = dict(version.body or {}) if version is not None else {}
        return {
            "id": str(result.id),
            "workerId": str(result.worker_id),
            "jobId": str(result.job_id),
            "status": result.status,
            "title": result.title,
            "summary": _clip(body.get("summary"), 1200),
            "createdAt": result.created_at.isoformat(),
        }

    async def _memories(
        self,
        organization_id: UUID,
        worker: Worker | None,
    ) -> list[dict[str, Any]]:
        owner_ids = [str(organization_id)]
        if worker is not None:
            owner_ids.append(str(worker.id))
        now = datetime.now(UTC)
        rows = await self._session.scalars(
            select(Memory)
            .where(
                Memory.organization_id == organization_id,
                Memory.owner_id.in_(owner_ids),
                Memory.status == "active",
                or_(Memory.expires_at.is_(None), Memory.expires_at > now),
            )
            .order_by(desc(Memory.created_at))
            .limit(MAX_MEMORIES)
        )
        return [
            {
                "id": str(item.id),
                "scope": item.scope,
                "type": item.memory_type,
                "title": _clip(item.title, 300),
                "content": _clip(item.content, 1200),
                "source": item.source,
                "provenance": dict(item.provenance or {}),
                "sensitivity": item.sensitivity,
                "expiresAt": item.expires_at.isoformat() if item.expires_at else None,
            }
            for item in rows.all()
        ]

    def _message_context(self, message: ConversationMessage) -> dict[str, Any]:
        return {
            "id": str(message.id),
            "role": message.role,
            "content": _clip(message.content, 2000),
            "artifactReferences": list(message.artifact_references or [])[:12],
            "commandReferences": list(message.command_references or [])[:12],
            "resultReferences": list(message.result_references or [])[:12],
            "createdAt": message.created_at.isoformat(),
        }

    def _artifact_ids(self, messages: list[ConversationMessage]) -> list[UUID]:
        ids: list[UUID] = []
        for message in messages:
            for raw in list(message.artifact_references or [])[:12]:
                value = raw.get("id") if isinstance(raw, dict) else raw
                try:
                    ids.append(UUID(str(value)))
                except (TypeError, ValueError):
                    continue
        return _ids(ids)[:MAX_ARTIFACTS]

    async def _artifacts(
        self,
        organization_id: UUID,
        artifact_ids: list[UUID],
    ) -> list[dict[str, Any]]:
        if not artifact_ids:
            return []
        rows = list(
            (
                await self._session.scalars(
                    select(Artifact).where(
                        Artifact.organization_id == organization_id,
                        Artifact.id.in_(artifact_ids),
                    )
                )
            ).all()
        )
        analyses = list(
            (
                await self._session.scalars(
                    select(ArtifactAnalysis).where(
                        ArtifactAnalysis.organization_id == organization_id,
                        ArtifactAnalysis.artifact_id.in_(artifact_ids),
                    )
                )
            ).all()
        )
        by_artifact = {item.artifact_id: item for item in analyses}
        result: list[dict[str, Any]] = []
        for item in rows:
            analysis = by_artifact.get(item.id)
            result.append(
                {
                    "id": str(item.id),
                    "mediaType": item.media_type,
                    "sizeBytes": item.size_bytes,
                    "checksumPresent": bool(item.checksum_sha256),
                    "trust": "untrusted_external_data",
                    "egress": "analysis_only",
                    "metadata": {
                        key: value
                        for key, value in dict(item.metadata_json or {}).items()
                        if key
                        in {
                            "name",
                            "filename",
                            "title",
                            "category",
                            "pageCount",
                            "sheetCount",
                            "language",
                        }
                    },
                    "analysis": (
                        {
                            "status": analysis.status,
                            "role": analysis.analyzer_role,
                            "provider": analysis.provider,
                            "model": analysis.model,
                            "findings": dict(analysis.findings or {}),
                            "provenance": dict(analysis.provenance or {}),
                        }
                        if analysis is not None
                        else None
                    ),
                }
            )
        return result
