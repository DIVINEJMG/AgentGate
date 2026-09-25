from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import Memory, Worker

_SECRET_PATTERNS = (
    re.compile(r"\bagt_sk_[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:sk|nvapi)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
)


def contains_secret_material(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


class WorkerMemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def seed_identity(
        self,
        *,
        worker: Worker,
        role: str,
        charter: str,
        responsibilities: list[str],
        source_id: str,
    ) -> Memory:
        content = (
            f"Role: {role}\nCharter: {charter}\nResponsibilities: {'; '.join(responsibilities)}"
        ).strip()
        if contains_secret_material(content):
            raise ValueError("Secret-like material cannot be stored in Worker memory.")
        existing = await self._session.scalar(
            select(Memory).where(
                Memory.organization_id == worker.organization_id,
                Memory.owner_id == str(worker.id),
                Memory.memory_type == "identity",
                Memory.status == "active",
            )
        )
        if existing is None:
            existing = Memory(
                organization_id=worker.organization_id,
                scope="worker",
                owner_id=str(worker.id),
                title=f"{worker.name} identity",
                content=content,
                memory_type="identity",
                source="worker_creation",
                provenance={"sourceType": "worker_draft", "sourceId": source_id},
                sensitivity="internal",
                status="active",
                expires_at=None,
            )
            self._session.add(existing)
        else:
            existing.title = f"{worker.name} identity"
            existing.content = content
            existing.source = "worker_creation"
            existing.provenance = {
                "sourceType": "worker_draft",
                "sourceId": source_id,
            }
            existing.sensitivity = "internal"
            existing.expires_at = None
        await self._session.flush()
        return existing

    async def add_standing_memory(
        self,
        *,
        worker: Worker,
        instruction: str,
        source_thread_id: UUID | None,
        source_message_id: UUID | None,
    ) -> Memory | None:
        clean = instruction.strip()
        if not clean or contains_secret_material(clean):
            return None
        memory = Memory(
            organization_id=worker.organization_id,
            scope="worker",
            owner_id=str(worker.id),
            title="Standing instruction",
            content=clean,
            memory_type="standing",
            source="human_instruction",
            provenance={
                "sourceType": "conversation",
                "threadId": str(source_thread_id) if source_thread_id else None,
                "messageId": str(source_message_id) if source_message_id else None,
            },
            sensitivity="internal",
            status="active",
            expires_at=None,
        )
        self._session.add(memory)
        await self._session.flush()
        return memory

    async def record_operational(
        self,
        *,
        worker: Worker,
        title: str,
        content: str,
        source_type: str,
        source_id: str,
        provenance: dict[str, Any] | None = None,
        expires_days: int = 90,
    ) -> Memory | None:
        clean = content.strip()
        if not clean or contains_secret_material(clean):
            return None
        memory = Memory(
            organization_id=worker.organization_id,
            scope="worker",
            owner_id=str(worker.id),
            title=title[:200] or "Operational memory",
            content=clean[:8000],
            memory_type="operational",
            source=source_type[:64],
            provenance={
                "sourceType": source_type,
                "sourceId": source_id,
                **(provenance or {}),
            },
            sensitivity="internal",
            status="active",
            expires_at=datetime.now(UTC) + timedelta(days=max(1, expires_days)),
        )
        self._session.add(memory)
        await self._session.flush()
        return memory

    async def record_episode(
        self,
        *,
        worker: Worker,
        run_id: UUID,
        result_id: UUID,
        summary: str,
    ) -> Memory | None:
        clean = summary.strip()
        if not clean or contains_secret_material(clean):
            return None
        memory = Memory(
            organization_id=worker.organization_id,
            scope="worker",
            owner_id=str(worker.id),
            title="Completed work episode",
            content=clean[:5000],
            memory_type="episodic",
            source="runtime_result",
            provenance={
                "sourceType": "result",
                "runId": str(run_id),
                "resultId": str(result_id),
            },
            sensitivity="internal",
            status="active",
            expires_at=datetime.now(UTC) + timedelta(days=180),
        )
        self._session.add(memory)
        await self._session.flush()
        return memory
