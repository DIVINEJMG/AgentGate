from collections.abc import Mapping
from dataclasses import dataclass
import contextvars
import json
import logging


_CONTEXT: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "audoryn_observability_context",
    default=None,
)


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    organization_id: str | None = None
    worker_id: str | None = None
    job_id: str | None = None
    work_item_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    action_id: str | None = None
    correlation_id: str | None = None

    def fields(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "organization_id": self.organization_id,
                "worker_id": self.worker_id,
                "job_id": self.job_id,
                "work_item_id": self.work_item_id,
                "run_id": self.run_id,
                "agent_id": self.agent_id,
                "action_id": self.action_id,
                "correlation_id": self.correlation_id,
            }.items()
            if value is not None
        }


def bind_context(context: ExecutionContext) -> contextvars.Token[dict[str, str] | None]:
    return _CONTEXT.set(context.fields())


def reset_context(token: contextvars.Token[dict[str, str] | None]) -> None:
    _CONTEXT.reset(token)


def structured_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    fields: Mapping[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {"event": event, **(_CONTEXT.get() or {})}
    if fields:
        payload.update(fields)
    logger.log(level, json.dumps(payload, separators=(",", ":"), sort_keys=True))
