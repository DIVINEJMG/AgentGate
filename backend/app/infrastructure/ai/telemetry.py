from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ai.providers import (
    AIErrorCategory,
    AIInvocationContext,
    AIModelRole,
)
from app.infrastructure.database.models import AIInvocation


class SQLAlchemyAIInvocationRecorder:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def start(
        self,
        *,
        context: AIInvocationContext,
        role: AIModelRole,
        provider: str,
        model: str,
        schema_name: str | None,
    ):
        row = AIInvocation(
            organization_id=context.organization_id,
            worker_id=context.worker_id,
            job_id=context.job_id,
            run_id=context.run_id,
            thread_id=context.thread_id,
            role=role,
            provider=provider,
            model=model,
            schema_name=schema_name,
            correlation_id=context.correlation_id,
            success=False,
            usage={},
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def finish(
        self,
        invocation_id,
        *,
        latency_ms: int,
        success: bool,
        request_id: str | None,
        usage: dict[str, int],
        error_category: AIErrorCategory | None,
    ) -> None:
        row = await self._session.get(AIInvocation, invocation_id)
        if row is None:
            return
        row.latency_ms = max(0, latency_ms)
        row.success = success
        row.request_id = request_id
        row.usage = dict(usage)
        row.error_category = error_category
        await self._session.flush()
