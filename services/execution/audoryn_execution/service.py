"""The execution service's part of a process (architecture plan, Phases 3 and 4).

A process with SERVICE_ROLE=execution (or "all") loads this module. It is the only place that
joins the execution code to the shared code:
- its routes are the QStash callbacks and the direct due-step calls;
- importing it gives the shared code what only the execution service has: running a due step
  here, the full sweep for the delivery watchdog, and the browser runtime (Playwright);
- start and stop run its background work: the preparation recovery, then on shutdown the step
  drain, the preparations and the providers' runtimes.

An "api" process never imports it, so the core needs neither Playwright nor the step runtime.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from fastapi import APIRouter, FastAPI

from audoryn_execution.api.internal.integrations import router as integrations_router
from audoryn_execution.api.internal.outbox import router as outbox_router
from audoryn_execution.api.internal.runtime import router as runtime_router
from audoryn_execution.api.internal.runtime import run_here, run_sweep, stop_runtime_tasks
from audoryn_execution.api.internal.web_research import router as web_research_router
from audoryn_execution.execution.browser.runtime import configured_runtime
from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.execution.bootstrap import execution_provider_registry
from audoryn_shared.execution.providers.base import ManagedExecutionProvider
from audoryn_shared.execution.providers.browser import browser_provider
from audoryn_shared.runtime import delivery_watchdog, qstash_trigger

logger = logging.getLogger(__name__)

ROUTERS: tuple[APIRouter, ...] = (integrations_router, outbox_router, runtime_router, web_research_router)
_recovery: asyncio.Task[None] | None = None


def install() -> None:
    qstash_trigger.local_runner = run_here
    delivery_watchdog.sweep = run_sweep
    browser_provider.use_runtime(configured_runtime)


install()


async def start() -> None:
    global _recovery
    if settings.runtime_execution_enabled:
        logger.info("Governed Chromium runtime is lazy; browser process will start on first browser action.")
    if settings.smart_planner_enabled and settings.integration_foundation_enabled:
        from audoryn_execution.runtime.preparation_worker import preparation_recovery_loop

        _recovery = asyncio.create_task(preparation_recovery_loop())


async def stop() -> None:
    """After the shared shutdown has stopped taking local steps and the watchdog."""
    global _recovery
    await stop_runtime_tasks(settings.runtime_drain_seconds)
    if _recovery is not None:
        _recovery.cancel()
        await asyncio.gather(_recovery, return_exceptions=True)
        _recovery = None
        from audoryn_execution.runtime.preparation_worker import stop_preparations

        await stop_preparations()
    for provider in execution_provider_registry().providers():
        if isinstance(provider, ManagedExecutionProvider):
            await provider.shutdown()


def create_application() -> FastAPI:
    """The execution service on its own (SERVICE_ROLE=execution)."""
    from audoryn_shared.bootstrap.application import build_application

    return build_application(ROUTERS, execution=sys.modules[__name__])
