from contextvars import ContextVar, Token
from json import dumps
from logging import INFO, Logger


_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "audoryn_observability_context",
    default=None,
)


class ExecutionContext:
    __slots__ = (
        "action_id",
        "agent_id",
        "correlation_id",
        "job_id",
        "organization_id",
        "run_id",
        "work_item_id",
        "worker_id",
    )

    def __init__(
        self,
        *,
        organization_id: str | None = None,
        worker_id: str | None = None,
        job_id: str | None = None,
        work_item_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        action_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.worker_id = worker_id
        self.job_id = job_id
        self.work_item_id = work_item_id
        self.run_id = run_id
        self.agent_id = agent_id
        self.action_id = action_id
        self.correlation_id = correlation_id

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


def bind_context(context: ExecutionContext) -> Token[dict[str, str] | None]:
    return _CONTEXT.set(context.fields())


def reset_context(token: Token[dict[str, str] | None]) -> None:
    _CONTEXT.reset(token)


def structured_event(
    logger: Logger,
    event: str,
    *,
    level: int = INFO,
    fields: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {"event": event, **(_CONTEXT.get() or {})}
    if fields:
        payload.update(fields)
    logger.log(level, dumps(payload, separators=(",", ":"), sort_keys=True))
