from urllib.parse import quote

import httpx

from app.bootstrap.settings import settings
from app.domain.queue.provider import QueueMessage, QueueProvider


def _destination_path(destination: str) -> str:
    """Preserve the URL scheme/path QStash expects while escaping URL query data."""
    return quote(destination, safe=":/")


class UpstashQStashProvider(QueueProvider):
    def __init__(self, *, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    @classmethod
    def from_settings(cls) -> "UpstashQStashProvider":
        if settings.qstash_url is None or settings.qstash_token is None:
            raise RuntimeError("QStash is not configured.")
        return cls(
            base_url=settings.qstash_url,
            token=settings.qstash_token.get_secret_value(),
        )

    def _headers(
        self,
        *,
        retries: int,
        timeout_seconds: int,
        idempotency_key: str | None = None,
        failure_callback: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Upstash-Method": "POST",
            "Upstash-Retries": str(retries),
            "Upstash-Timeout": f"{timeout_seconds}s",
        }
        if idempotency_key:
            headers["Upstash-Deduplication-Id"] = idempotency_key
        callback = failure_callback or settings.qstash_failure_callback_url
        if callback:
            headers["Upstash-Failure-Callback"] = callback
        return headers

    async def publish(
        self,
        *,
        destination: str,
        body: str,
        idempotency_key: str,
        retries: int = 3,
        timeout_seconds: int = 15,
        failure_callback: str | None = None,
    ) -> QueueMessage:
        encoded = _destination_path(destination)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/v2/publish/{encoded}",
                headers=self._headers(
                    retries=retries,
                    timeout_seconds=timeout_seconds,
                    idempotency_key=idempotency_key,
                    failure_callback=failure_callback,
                ),
                content=body,
            )
            response.raise_for_status()
            payload = response.json()
        return QueueMessage(
            id=str(payload["messageId"]),
            deduplicated=bool(payload.get("deduplicated", False)),
        )

    async def schedule(
        self,
        *,
        destination: str,
        cron: str,
        body: str,
        retries: int = 3,
        timeout_seconds: int = 15,
        failure_callback: str | None = None,
    ) -> str:
        encoded = _destination_path(destination)
        headers = self._headers(
            retries=retries,
            timeout_seconds=timeout_seconds,
            failure_callback=failure_callback,
        )
        headers["Upstash-Cron"] = cron
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/v2/schedules/{encoded}",
                headers=headers,
                content=body,
            )
            response.raise_for_status()
            payload = response.json()
        return str(payload["scheduleId"])

    async def cancel(self, schedule_id: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.delete(
                f"{self._base_url}/v2/schedules/{quote(schedule_id, safe='')}",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            if response.status_code != 404:
                response.raise_for_status()

    async def retry(self, dlq_id: str) -> QueueMessage:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/v2/dlq/retry/{quote(dlq_id, safe='')}",
                headers={"Authorization": f"Bearer {self._token}"},
            )
            response.raise_for_status()
            payload = response.json()
        return QueueMessage(
            id=str(payload["messageId"]),
            deduplicated=bool(payload.get("deduplicated", False)),
        )

    async def healthcheck(self) -> bool:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{self._base_url}/v2/schedules",
                headers={"Authorization": f"Bearer {self._token}"},
            )
        return response.status_code == 200
