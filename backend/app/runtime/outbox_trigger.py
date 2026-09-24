from __future__ import annotations

import asyncio
import json
import logging
from uuid import uuid4

from app.bootstrap.settings import settings
from app.infrastructure.qstash.provider import UpstashQStashProvider

logger = logging.getLogger(__name__)


async def request_outbox_drain(*, reason: str) -> str | None:
    """Ask QStash to invoke the protected drain endpoint.

    Failure to enqueue is deliberately non-fatal for the committed business
    transaction. The periodic QStash recovery schedule will drain any missed rows.
    """

    if (
        settings.qstash_outbox_drain_url is None
        or settings.qstash_url is None
        or settings.qstash_token is None
    ):
        return None

    provider = UpstashQStashProvider.from_settings()
    message = await provider.publish(
        destination=settings.qstash_outbox_drain_url,
        body=json.dumps({"reason": reason}, separators=(",", ":")),
        idempotency_key=f"outbox-drain:{uuid4()}",
        retries=3,
        timeout_seconds=20,
    )
    return message.id


def request_outbox_drain_after_commit(*, reason: str) -> None:
    """Best-effort immediate QStash signal from SQLAlchemy's after_commit hook."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    task = loop.create_task(request_outbox_drain(reason=reason))

    def _consume_result(completed: asyncio.Task[str | None]) -> None:
        if completed.cancelled():
            logger.warning("Post-commit QStash outbox signal was cancelled.")
            return
        error = completed.exception()
        if error is not None:
            # Never turn an already-successful DB commit into an application failure.
            # The scheduled QStash recovery sweep is the durability backstop.
            logger.warning(
                "Post-commit QStash outbox signal failed; recovery sweep will retry: %s",
                error,
            )

    task.add_done_callback(_consume_result)
