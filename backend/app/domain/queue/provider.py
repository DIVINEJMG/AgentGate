from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class QueueMessage:
    id: str
    deduplicated: bool = False


class QueueProvider(Protocol):
    async def publish(
        self,
        *,
        destination: str,
        body: str,
        idempotency_key: str,
        retries: int = 3,
        timeout_seconds: int = 15,
        failure_callback: str | None = None,
    ) -> QueueMessage: ...

    async def schedule(
        self,
        *,
        destination: str,
        cron: str,
        body: str,
        retries: int = 3,
        timeout_seconds: int = 15,
        failure_callback: str | None = None,
    ) -> str: ...

    async def cancel(self, schedule_id: str) -> None: ...

    async def retry(self, dlq_id: str) -> QueueMessage: ...

    async def healthcheck(self) -> bool: ...
