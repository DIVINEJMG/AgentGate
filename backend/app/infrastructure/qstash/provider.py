import hashlib
from urllib.parse import quote

import httpx

from app.bootstrap.settings import settings
from app.domain.queue.provider import QueueMessage, QueueProvider


def _destination_path(destination: str) -> str:
    """Preserve the URL scheme/path QStash expects while escaping URL query data."""
    return quote(destination, safe=":/")


def _deduplication_id(idempotency_key: str) -> str:
    """Map arbitrary internal idempotency keys to QStash-safe stable IDs."""
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"audoryn-{digest}"


class QStashRateLimitedError(RuntimeError):
    def __init__(self, message: str, *, daily_quota_exhausted: bool) -> None:
        super().__init__(message)
        self.daily_quota_exhausted = daily_quota_exhausted


class QStashRequestRejectedError(RuntimeError):
    """QStash rejected a publish request before creating a delivery."""


def _rejection_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "message"):
            detail = payload.get(key)
            if isinstance(detail, str) and detail.strip():
                token = settings.qstash_token
                if token is not None:
                    detail = detail.replace(token.get_secret_value(), "[redacted]")
                return detail.strip()[:300]
    return "QStash did not provide a structured rejection reason."


def raise_for_qstash_status(response: httpx.Response) -> None:
    if response.status_code == 429:
        detail = response.text.strip()
        lowered = detail.lower()
        daily_quota_exhausted = any(
            marker in lowered
            for marker in (
                "daily ratelimit",
                "daily rate limit",
                "daily quota",
                "limit 1000 exceeded",
            )
        )
        raise QStashRateLimitedError(
            detail or "QStash rate limit reached.",
            daily_quota_exhausted=daily_quota_exhausted,
        )
    if response.status_code == 400:
        raise QStashRequestRejectedError(
            f"QStash rejected the publish request (400): {_rejection_detail(response)}"
        )
    response.raise_for_status()


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
        delay_seconds: int | None = None,
        flow_control_key: str | None = None,
        parallelism: int | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Upstash-Method": "POST",
            "Upstash-Retries": str(retries),
            "Upstash-Timeout": f"{timeout_seconds}s",
        }
        if idempotency_key:
            headers["Upstash-Deduplication-Id"] = _deduplication_id(idempotency_key)
        if delay_seconds is not None and delay_seconds > 0:
            headers["Upstash-Delay"] = f"{int(delay_seconds)}s"
        if flow_control_key and parallelism is not None:
            headers["Upstash-Flow-Control-Key"] = flow_control_key
            headers["Upstash-Flow-Control-Value"] = f"parallelism={max(1, parallelism)}"
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
        delay_seconds: int | None = None,
        flow_control_key: str | None = None,
        parallelism: int | None = None,
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
                    delay_seconds=delay_seconds,
                    flow_control_key=flow_control_key,
                    parallelism=parallelism,
                ),
                content=body,
            )
            raise_for_qstash_status(response)
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
            raise_for_qstash_status(response)
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
