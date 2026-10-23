"""The rolling restart (Phase 2), over real servers: copies are stopped and started again on their
ports while requests keep coming through the gateway."""
from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Iterator

import httpx
import pytest

from audoryn_gateway.app import Gateway
from audoryn_gateway.rollout import Copy, Rollout, RolloutError, shell
from audoryn_gateway.settings import GatewaySettings
from tests.conftest import ADMIN, Server, make_core


class Fleet:
    """Two core copies, their gateway, and a restart that really stops and starts a copy."""

    def __init__(self, *, execution_on_first_only: bool) -> None:
        self.copies = {server.url: server for server in (Server(make_core("a")), Server(make_core("b")))}
        urls = list(self.copies)
        self.gateway = Server(Gateway(GatewaySettings(
            core_upstreams=urls, execution_upstreams=urls[:1] if execution_on_first_only else [],  # type: ignore[arg-type]
            health_interval_seconds=0.1, unhealthy_after=1, healthy_after=1, admin_token=ADMIN)))  # type: ignore[arg-type]
        self.url = self.gateway.url
        self.restarted: list[str] = []

    async def restart(self, copy: Copy) -> None:
        old = self.copies[copy["url"]]
        await asyncio.to_thread(old.stop)
        self.copies[copy["url"]] = await asyncio.to_thread(Server, make_core(copy["name"]), old.port)
        self.restarted.append(copy["name"])

    def stop(self) -> None:
        for server in (self.gateway, *self.copies.values()):
            server.stop()


@pytest.fixture
def fleet() -> Iterator[Fleet]:
    running = Fleet(execution_on_first_only=False)
    yield running
    running.stop()


@pytest.fixture
def one_execution_copy() -> Iterator[Fleet]:
    running = Fleet(execution_on_first_only=True)
    yield running
    running.stop()


async def test_a_rolling_restart_under_load_fails_no_request(fleet: Fleet):
    statuses: Counter[int | str] = Counter()
    done, rolling = asyncio.Event(), asyncio.Event()
    during = 0  # answered while the copies restart: proof the load ran through them, at any machine speed

    async def load(client: httpx.AsyncClient, method: str) -> None:
        nonlocal during
        while not done.is_set():
            try:
                statuses[(await client.request(method, f"{fleet.url}/api/v1/echo", content=b"x")).status_code] += 1
                during += rolling.is_set()
            except httpx.HTTPError as error:
                statuses[type(error).__name__] += 1
            await asyncio.sleep(0.01)

    async with httpx.AsyncClient(timeout=10) as client:
        loaders = [asyncio.create_task(load(client, method)) for method in ("GET", "POST") * 4]
        await asyncio.sleep(0.3)
        rolling.set()
        await Rollout(client, fleet.url, ADMIN, say=lambda _: None).run(["core-0", "core-1"], fleet.restart)
        rolling.clear()
        await asyncio.sleep(0.3)
        done.set()
        await asyncio.gather(*loaders)
        listed = await Rollout(client, fleet.url, ADMIN).copies()
    assert fleet.restarted == ["core-0", "core-1"]
    assert set(statuses) == {200} and during >= 8  # the load ran through both restarts
    assert all(copy["healthy"] and not copy["draining"] for copy in listed)


async def test_the_only_execution_copy_restarts_only_when_that_pool_may_pause(one_execution_copy: Fleet):
    fleet = one_execution_copy
    seen: list[int] = []

    async def restart(copy: Copy) -> None:
        async with httpx.AsyncClient() as client:  # meanwhile QStash's deliveries are refused, and retried later
            seen.append((await client.post(f"{fleet.url}/internal/v1/runtime/execute")).status_code)
        await fleet.restart(copy)

    async with httpx.AsyncClient(timeout=10) as client:
        with pytest.raises(RolloutError, match="only copy in rotation of execution"):
            await Rollout(client, fleet.url, ADMIN, say=lambda _: None).restart("core-0", restart)
        assert not (await Rollout(client, fleet.url, ADMIN).copies())[0]["draining"]  # refused before draining
        await Rollout(client, fleet.url, ADMIN, pause_last="execution", say=lambda _: None).restart("core-0", restart)
        assert seen == [502]
        assert (await client.post(f"{fleet.url}/internal/v1/runtime/execute")).json() == {"copy": "core-0"}


async def test_pausing_the_execution_pool_never_empties_the_core_pool(one_execution_copy: Fleet):
    fleet = one_execution_copy
    async with httpx.AsyncClient(timeout=10) as client:
        rollout = Rollout(client, fleet.url, ADMIN, pause_last="execution", say=lambda _: None)
        await rollout._admin("POST", "copies/core-1/drain")
        with pytest.raises(RolloutError, match="core, execution"):
            await rollout.restart("core-0", fleet.restart)
        assert fleet.restarted == []
        await rollout._admin("POST", "copies/core-1/restore")


async def test_a_failed_restart_command_stops_the_rollout():
    with pytest.raises(RolloutError, match="exited with 3"):
        await shell("exit 3")({"name": "core-0", "url": "http://127.0.0.1:8101"})
