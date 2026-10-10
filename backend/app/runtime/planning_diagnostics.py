"""Sanitized planning diagnostics: never log SQL, prompts or provider bodies."""
import logging

from sqlalchemy.exc import SQLAlchemyError

from app.domain.ai.providers import AIProviderError

logger = logging.getLogger("uvicorn.error")


def database_planning_error(error: SQLAlchemyError, *, work_item_id, run_id) -> AIProviderError:
    cause: BaseException | None = error
    sqlstate = None
    for _ in range(6):
        if cause is None:
            break
        sqlstate = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        if sqlstate:
            break
        cause = getattr(cause, "orig", None) or cause.__cause__
    setup_failure = sqlstate in {"42P01", "42703", "42501"}
    reason = (
        "database_schema_missing" if sqlstate in {"42P01", "42703"}
        else "database_permission_denied" if sqlstate == "42501"
        else "database_preparation_failed"
    )
    logger.error(
        "Runtime planning failed work_item=%s run=%s phase=database_preparation "
        "model=not_called reason=%s sqlstate=%s error_type=%s retryable=%s",
        work_item_id, run_id, reason, sqlstate or "unknown", type(error).__name__, not setup_failure,
    )
    return AIProviderError(
        "configuration_missing" if setup_failure else "provider_unavailable",
        "Planning database setup is incomplete. Check migrations and runtime database permissions."
        if setup_failure else "Planning preparation could not access its saved state.",
        retryable=not setup_failure,
    )
