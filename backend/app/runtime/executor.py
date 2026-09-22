from dataclasses import dataclass
from typing import Protocol

from app.domain.jobs.queue import ClaimedWorkItem


@dataclass(frozen=True, slots=True)
class WorkExecutionResult:
    summary: str


class RuntimeExecutor(Protocol):
    async def execute(self, work_item: ClaimedWorkItem) -> WorkExecutionResult: ...


class UnconfiguredRuntimeExecutor(RuntimeExecutor):
    async def execute(self, work_item: ClaimedWorkItem) -> WorkExecutionResult:
        raise RuntimeError("Runtime execution is not configured yet.")
