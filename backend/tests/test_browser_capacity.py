from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest
from playwright.async_api import Browser

from app.execution.browser.errors import BrowserCapacityUnavailable, BrowserOwnershipLost
from app.execution.browser.runtime import BrowserRuntime
from app.infrastructure.redis.coordination import Lease, RedisCoordinator


class FakeCoordinator:
    def __init__(self):
        self.locks = {}
        self.generations = {}

    async def acquire_lock(self, key, *, ttl_seconds):
        if key in self.locks:
            return None
        lease = Lease(key=key, token=str(uuid4()))
        self.locks[key] = lease
        return lease

    async def release_lock(self, lease):
        if self.locks.get(lease.key) == lease:
            del self.locks[lease.key]
            return True
        return False

    async def renew_lock(self, lease, *, ttl_seconds):
        return self.locks.get(lease.key) == lease

    async def next_fencing_token(self, key):
        self.generations[key] = self.generations.get(key, 0) + 1
        return self.generations[key]

    async def current_fencing_token(self, key):
        return self.generations.get(key, 0)


class FakePage:
    url = "about:blank"

    def on(self, *_args):
        pass

    def is_closed(self):
        return False


class FakeContext:
    def on(self, *_args):
        pass

    async def new_page(self):
        return FakePage()

    async def route(self, *_args):
        pass

    async def close(self):
        pass


class FakeBrowser:
    def __init__(self):
        self.contexts = 0

    async def new_context(self, **_kwargs):
        self.contexts += 1
        return FakeContext()


@pytest.mark.asyncio
async def test_browser_contexts_share_warm_engine_but_obey_global_and_org_slots(monkeypatch):
    coordinator = FakeCoordinator()
    browser = FakeBrowser()
    runtime = BrowserRuntime(
        coordinator=cast(RedisCoordinator, coordinator), max_active_sessions=2,
        max_sessions_per_organization=1, idle_shutdown_seconds=180,
    )

    async def ensure_browser():
        runtime._browser = cast(Browser, browser)
        return browser

    monkeypatch.setattr(runtime, "_ensure_browser", ensure_browser)
    monkeypatch.setattr(runtime, "_memory_snapshot", lambda: SimpleNamespace(cgroup_percent=None))
    monkeypatch.setattr(runtime, "_log_memory", lambda *_args: None)
    first_org, second_org = uuid4(), uuid4()
    first = await runtime.create_session(
        organization_id=first_org, worker_id=None, run_id=uuid4()
    )
    assert first.expires_at - first.created_at == timedelta(minutes=15)
    second = await runtime.create_session(
        organization_id=second_org, worker_id=None, run_id=uuid4()
    )
    assert browser.contexts == 2
    with pytest.raises(BrowserCapacityUnavailable):
        await runtime.create_session(
            organization_id=first_org, worker_id=None, run_id=uuid4()
        )
    await runtime.close(first.id)
    third = await runtime.create_session(
        organization_id=first_org, worker_id=None, run_id=uuid4()
    )
    assert browser.contexts == 3
    await runtime.close(second.id)
    await runtime.close(third.id)
    runtime._cancel_idle_shutdown()
    assert not coordinator.locks


@pytest.mark.asyncio
async def test_superseded_browser_owner_cannot_resume(monkeypatch):
    coordinator = FakeCoordinator()
    runtime = BrowserRuntime(coordinator=cast(RedisCoordinator, coordinator))
    browser = FakeBrowser()

    async def ensure_browser():
        runtime._browser = cast(Browser, browser)
        return browser

    monkeypatch.setattr(runtime, "_ensure_browser", ensure_browser)
    monkeypatch.setattr(runtime, "_memory_snapshot", lambda: SimpleNamespace(cgroup_percent=None))
    monkeypatch.setattr(runtime, "_log_memory", lambda *_args: None)
    organization, run = uuid4(), uuid4()
    session = await runtime.create_session(
        organization_id=organization, worker_id=None, run_id=run
    )
    coordinator.generations[f"browser-run:{run}"] += 1

    with pytest.raises(BrowserOwnershipLost):
        await runtime.resume(
            session.id, organization_id=organization, worker_id=None
        )
    runtime._cancel_idle_shutdown()
    assert not coordinator.locks


@pytest.mark.asyncio
async def test_resume_renews_browser_idle_window_and_ownership(monkeypatch):
    coordinator = FakeCoordinator()
    runtime = BrowserRuntime(coordinator=cast(RedisCoordinator, coordinator))
    browser = FakeBrowser()

    async def ensure_browser():
        runtime._browser = cast(Browser, browser)
        return browser

    monkeypatch.setattr(runtime, "_ensure_browser", ensure_browser)
    monkeypatch.setattr(runtime, "_memory_snapshot", lambda: SimpleNamespace(cgroup_percent=None))
    monkeypatch.setattr(runtime, "_log_memory", lambda *_args: None)
    organization, run = uuid4(), uuid4()
    session = await runtime.create_session(
        organization_id=organization, worker_id=None, run_id=run
    )
    handle = runtime._sessions[session.id]
    handle.session = replace(handle.session, expires_at=datetime.now(UTC) + timedelta(minutes=1))
    resumed = await runtime.resume(session.id, organization_id=organization, worker_id=None)
    assert resumed.expires_at > datetime.now(UTC) + timedelta(minutes=14)
    assert coordinator.locks
    await runtime.close(session.id)
    runtime._cancel_idle_shutdown()
