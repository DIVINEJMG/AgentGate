import json
from collections.abc import Mapping

from app.domain.queue.provider import QueueProvider


class ProviderQueueBroker:
    """Application queue boundary backed by a replaceable QueueProvider."""

    def __init__(self, provider: QueueProvider) -> None:
        self._provider = provider

    async def publish(
        self,
        destination: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> str:
        message = await self._provider.publish(
            destination=destination,
            body=json.dumps(dict(payload), separators=(",", ":"), sort_keys=True),
            idempotency_key=idempotency_key,
        )
        return message.id
