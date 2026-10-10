"""Backend-selected workload; model roles describe output, not execution complexity."""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4

AIWorkload = Literal["interactive", "complex"]


def for_workload[GatewayT](gateway: GatewayT, workload: AIWorkload, purpose: str) -> GatewayT:
    select = getattr(gateway, "with_workload", None)
    return cast(GatewayT, select(workload, purpose)) if callable(select) else gateway


async def acquire_workload_slot(coordinator, config, org, workload, ttl_seconds):
    # Background work cannot consume the seats reserved for human requests.
    # Both classes still acquire the same total ceiling and share request/token budgets.
    background = None
    if workload == "complex":
        limit = config.smart_planner_org_concurrency - config.ai_interactive_reserved_slots
        if limit <= 0:
            return None
        background = await coordinator.acquire_slot(
            f"planner:complex:{org}", limit=limit, ttl_seconds=ttl_seconds
        )
        if background is None:
            return None
    try:
        total = await coordinator.acquire_slot(
            f"planner:org:{org}",
            limit=config.smart_planner_org_concurrency,
            ttl_seconds=ttl_seconds,
        )
        if total is None and background:
            await coordinator.release_slot(background)
        return (total, background) if total else None
    except BaseException:
        if background:
            await coordinator.release_slot(background)
        raise


async def release_workload_slot(coordinator, slots):
    if slots:
        try:
            await coordinator.release_slot(slots[0])
        finally:
            if slots[1]:
                await coordinator.release_slot(slots[1])


async def acquire_account_slot(coordinator, config, account, workload, ttl_seconds):
    # Reserve account capacity across organizations as well as within each organization.
    background = None
    if workload == "complex":
        limit = config.ai_account_concurrency - config.ai_interactive_reserved_slots
        if limit <= 0:
            return None
        background = await coordinator.acquire_slot(
            account + ":complex-concurrency", limit=limit, ttl_seconds=ttl_seconds
        )
        if background is None:
            return None
    try:
        total = await coordinator.acquire_slot(
            account + ":concurrency", limit=config.ai_account_concurrency, ttl_seconds=ttl_seconds
        )
        if total is None and background:
            await coordinator.release_slot(background)
        return (total, background) if total else None
    except BaseException:
        if background:
            await coordinator.release_slot(background)
        raise


logger = logging.getLogger("uvicorn.error")


@dataclass
class BudgetReservation:
    identity: str
    entries: list[dict]
    bound: int


async def reserve_workload_budgets(coordinator, config, org, workload, token_bound):
    from app.domain.ai.providers import AIProviderError

    day = datetime.now(UTC).date()
    minute = int(datetime.now(UTC).timestamp()) // 60
    entries = []

    def add(key, kind, limit, ttl, scope):
        entries.append({"key": key, "kind": kind, "units": token_bound if kind == "tokens" else 1,
                        "limit": limit, "ttl": ttl, "scope": scope})

    if workload == "complex":
        add(f"planner:complex-requests:{org}:{minute}", "requests",
            config.smart_planner_org_requests_per_minute - config.ai_interactive_reserved_requests,
            60, "complex_requests")
        add(f"planner:complex-tokens:{org}:{day}", "tokens",
            config.smart_planner_org_tokens_per_day - config.ai_interactive_reserved_tokens,
            86400, "complex_tokens")
    add(f"planner:requests:{org}:{minute}", "requests", config.smart_planner_org_requests_per_minute,
        60, "organization_requests")
    add(f"planner:tokens:{org}:{day}", "tokens", config.smart_planner_org_tokens_per_day,
        86400, "organization_tokens")
    reservation = BudgetReservation(str(uuid4()), entries, token_bound)
    rejected = await coordinator.reserve_ai_budget(reservation.identity, entries)
    if rejected >= 0:
        entry = entries[rejected]
        logger.warning("AI admission blocked workload=%s organization=%s model_called=false budget=%s requested_units=%s limit=%s",
            workload, org, entry["scope"], entry["units"], entry["limit"])
        raise AIProviderError("quota_exhausted" if entry["kind"] == "tokens" else "rate_limited",
            "The local workspace AI budget blocked this request.", retryable=entry["kind"] == "requests",
            organization_scoped=True, retry_after_seconds=60 if entry["kind"] == "requests" else None,
            budget_scope=entry["scope"], model_called=False)
    return reservation


async def settle_workload_budget(coordinator, reservation, *, called, usage=None):
    if reservation is None:
        return
    # Unknown usage after dispatch may still be charged by the provider. Preserve the
    # reservation rather than inventing a zero charge on timeouts or incomplete output.
    actual, basis = (reservation.bound, "conservative_unknown") if called else (0, "not_called")
    if called and isinstance(usage, dict):
        total = usage.get("total_tokens")
        if type(total) is not int:
            prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
            total = prompt + completion if type(prompt) is int and type(completion) is int else None
        if type(total) is int and total >= 0:
            actual, basis = total, "reported_usage"
    try:
        applied = await asyncio.wait_for(coordinator.settle_ai_budget(reservation.identity, reservation.entries,
            actual_tokens=actual, called=called), timeout=5)
        if not applied:
            logger.warning("AI budget settlement skipped reservation=%s reason=not_pending_or_expired model_called=%s",
                reservation.identity, str(called).lower())
            return
        logger.info("AI budget settled reservation=%s model_called=%s reserved_tokens=%s charged_tokens=%s basis=%s",
            reservation.identity, str(called).lower(), reservation.bound, actual, basis)
    except Exception as error:  # noqa: BLE001 - reporting must not replace an inference outcome
        logger.warning("AI budget settlement unavailable reservation=%s model_called=%s error_type=%s; conservative reservation retained",
            reservation.identity, str(called).lower(), type(error).__name__)


def account_identity(config, provider, credential):
    identity = hashlib.sha256(
        json.dumps(credential or "unconfigured", sort_keys=True).encode()
    ).hexdigest()[:16]
    return f"planner:account:{config.environment}:{provider}:{identity}"
