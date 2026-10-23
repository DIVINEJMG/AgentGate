"""The copies behind each pool: which ones may take requests, and which one takes the next.

- **Health:** each copy's `health_path` (`/health/live`) is checked every few seconds. After `unhealthy_after`
  failures in a row a copy leaves rotation, and after `healthy_after` successes it comes back.
  Failing to connect takes it out at once, and the checks bring it back.
- **Draining:** a drained copy gets no new requests, so it can be restarted once its open
  requests reach zero. Open WebSockets stay until the copy closes them on shutdown, and the
  caller reconnects through another copy.
- **Balancing:** least open requests, with ties taken in turn.

Health is each gateway's own view, from its own checks. Drains are shared: with Redis configured,
a drain made through any gateway copy reaches every copy within one check interval.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError

from audoryn_gateway.routes import Pool
from audoryn_gateway.settings import GatewaySettings

logger = logging.getLogger("audoryn.gateway")
_DRAIN_SECONDS = 6 * 3600  # a drain left behind lapses; the health checks still guard a stopped copy


@dataclass
class Copy:
    name: str
    url: str
    healthy: bool = True  # until a check says otherwise, so the first requests are not refused
    draining: bool = False
    requests: int = 0  # open HTTP requests
    sockets: int = 0  # open WebSockets
    failures: int = 0
    successes: int = 0

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "url": self.url, "healthy": self.healthy, "draining": self.draining,
                "requests": self.requests, "sockets": self.sockets}


@dataclass
class Pools:
    settings: GatewaySettings
    redis: Redis | None = None
    prefix: str = "audoryn:gateway"
    copies: dict[Pool, list[Copy]] = field(default_factory=dict)
    _turn: Iterator[int] = field(default_factory=itertools.count)

    def __post_init__(self) -> None:
        core = [str(url).rstrip("/") for url in self.settings.core_upstreams]
        execution = [str(url).rstrip("/") for url in self.settings.execution_upstreams] or core
        # One Copy per address, shared by both pools while they hold the same copies (before Phase 3).
        known: dict[str, Copy] = {}
        pools: tuple[tuple[Pool, list[str]], ...] = (("core", core), ("execution", execution))
        for pool, urls in pools:
            self.copies[pool] = [known.setdefault(url, Copy(f"{pool}-{index}", url)) for index, url in enumerate(urls)]

    def all(self) -> list[Copy]:
        return list({id(copy): copy for copies in self.copies.values() for copy in copies}.values())

    def choose(self, pool: Pool, avoid: Copy | None = None) -> Copy | None:
        """The copy with the fewest open requests among those in rotation; None when there is none."""
        ready = [copy for copy in self.copies[pool] if copy.healthy and not copy.draining and copy is not avoid]
        if not ready:
            return None
        turn = next(self._turn)
        return min(ready, key=lambda copy: (copy.requests + copy.sockets, (ready.index(copy) - turn) % len(ready)))

    def retry_on(self, pool: Pool, failed: Copy) -> Copy | None:
        """Where a retry goes: another copy in rotation, or else the same one (on a fresh
        connection) while it is still in rotation, as when a connection it had just closed was
        reused. A copy that could not be reached is out of rotation already."""
        return self.choose(pool, avoid=failed) or (failed if failed.healthy and not failed.draining else None)

    def last_in_rotation(self, copy: Copy) -> bool:
        """Draining this copy would leave one of its pools with none to take requests."""
        return any(copy in copies and not any(other.healthy and not other.draining
                                              for other in copies if other is not copy)
                   for copies in self.copies.values())

    def pools_of(self, copy: Copy) -> list[Pool]:
        return [pool for pool, copies in self.copies.items() if copy in copies]

    def find(self, name: str) -> Copy | None:
        return next((copy for copy in self.all() if copy.name == name), None)

    @contextmanager
    def using(self, copy: Copy, *, socket: bool = False) -> Iterator[None]:
        if socket:
            copy.sockets += 1
        else:
            copy.requests += 1
        try:
            yield
        finally:
            if socket:
                copy.sockets -= 1
            else:
                copy.requests -= 1

    async def set_draining(self, copy: Copy, draining: bool) -> None:
        copy.draining = draining
        if self.redis is not None:
            key = f"{self.prefix}:drain:{copy.url}"
            try:
                await (self.redis.set(key, "1", ex=_DRAIN_SECONDS) if draining else self.redis.delete(key))
            except (RedisError, OSError, TimeoutError) as error:
                logger.warning("Drain of %s kept in this gateway only: Redis unavailable (%s)",
                               copy.name, type(error).__name__)

    async def _sync_drains(self) -> None:
        if self.redis is None:
            return
        copies = self.all()
        try:
            flags = await self.redis.mget([f"{self.prefix}:drain:{copy.url}" for copy in copies])
        except (RedisError, OSError, TimeoutError):
            return  # keep the drains already known
        for copy, flag in zip(copies, flags, strict=True):
            copy.draining = flag is not None

    def unreachable(self, copy: Copy) -> None:
        """A connection to the copy failed: out of rotation now; the checks bring it back."""
        if copy.healthy:
            logger.warning("Upstream %s unreachable; out of rotation until its checks pass", copy.name)
        copy.healthy, copy.successes = False, 0

    def _record(self, copy: Copy, ok: bool) -> None:
        settings = self.settings
        if ok:
            copy.failures, copy.successes = 0, copy.successes + 1
            if not copy.healthy and copy.successes >= settings.healthy_after:
                copy.healthy = True
                logger.warning("Upstream %s healthy again; back in rotation", copy.name)
        else:
            copy.successes, copy.failures = 0, copy.failures + 1
            if copy.healthy and copy.failures >= settings.unhealthy_after:
                copy.healthy = False
                logger.warning("Upstream %s failed %d checks; out of rotation", copy.name, copy.failures)

    async def check(self, client: httpx.AsyncClient) -> None:
        async def one(copy: Copy) -> None:
            try:
                response = await client.get(f"{copy.url}{self.settings.health_path}",
                                            timeout=self.settings.health_timeout_seconds)
                ok = response.status_code == 200
            except httpx.HTTPError:
                ok = False
            self._record(copy, ok)

        await asyncio.gather(*(one(copy) for copy in self.all()), self._sync_drains())

    async def watch(self, client: httpx.AsyncClient) -> None:
        while True:
            await self.check(client)
            await asyncio.sleep(self.settings.health_interval_seconds)
