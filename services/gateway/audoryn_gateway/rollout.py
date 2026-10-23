"""Rolling restart (architecture plan, Phase 2): restarts the copies one at a time, so no request fails.

For each copy:
1. drain it at the gateway (no new requests);
2. wait until its open requests reach 0;
3. run the restart command: a graceful stop, then a start. The core lets running steps finish within
   RUNTIME_DRAIN_SECONDS (Phase 0);
4. wait until the copy answers its liveness check;
5. restore it, and wait until the gateway has it back in rotation before the next copy.

It stops at the first copy that fails and leaves that copy drained; the others keep serving.

    python -m audoryn_gateway.rollout --restart "sudo systemctl restart audoryn-core@{port}"

`{name}`, `{url}` and `{port}` in the command are the copy's. The admin token comes from
GATEWAY_ADMIN_TOKEN, as for the gateway. A copy that is the only one in rotation of a pool (the
execution copy, until Phase 3) is refused unless that pool is named in `--pause-last execution`: the
pool then answers 502 during the restart, and QStash retries its deliveries. Every other pool of the
copy must still have another copy in rotation.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx

Copy = dict[str, Any]  # as the gateway lists it
Restart = Callable[[Copy], Awaitable[None]]


class RolloutError(RuntimeError):
    pass


class Rollout:
    def __init__(self, client: httpx.AsyncClient, gateway: str, token: str, *, pause_last: str | None = None,
                 idle_seconds: float = 120, start_seconds: float = 300,
                 say: Callable[[str], None] = print) -> None:
        self.client, self.gateway, self.token, self.pause_last = client, gateway.rstrip("/"), token, pause_last
        self.idle_seconds, self.start_seconds, self.say = idle_seconds, start_seconds, say

    async def copies(self) -> list[Copy]:
        return (await self._admin("GET", "copies")).json()["copies"]

    async def run(self, names: list[str], restart: Restart) -> None:
        for name in names:
            await self.restart(name, restart)

    async def restart(self, name: str, restart: Restart) -> None:
        if (await self._admin("POST", f"copies/{name}/drain")).status_code == 409:
            alone = await self._alone_in(name)
            if alone != {self.pause_last}:
                raise RolloutError(f"{name} is the only copy in rotation of {', '.join(sorted(alone))}; "
                                   "only a pool named in --pause-last may pause.")
            await self._admin("POST", f"copies/{name}/drain", last=True)
            self.say(f"{name}: the {self.pause_last} pool pauses until it is back")
        self.say(f"{name}: drained")
        if not await self._until(lambda copy: copy["requests"] == 0, name, self.idle_seconds):
            self.say(f"{name}: requests still open after {self.idle_seconds:.0f}s; the graceful stop waits for them")
        began = time.monotonic()
        await restart(await self._copy(name))
        self.say(f"{name}: restarted in {time.monotonic() - began:.1f}s")
        url = str((await self._copy(name))["url"])
        if not await self._answers(url):
            raise RolloutError(f"{name} did not answer {url}/health/live within {self.start_seconds:.0f}s; "
                               "it stays drained.")
        await self._admin("POST", f"copies/{name}/restore")
        if not await self._until(lambda copy: copy["healthy"] and not copy["draining"], name, self.start_seconds):
            raise RolloutError(f"The gateway did not put {name} back in rotation within {self.start_seconds:.0f}s.")
        self.say(f"{name}: back in rotation")

    async def _admin(self, method: str, path: str, *, last: bool = False) -> httpx.Response:
        response = await self.client.request(method, f"{self.gateway}/gateway/admin/{path}",
                                             params={"last": "true"} if last else None,
                                             headers={"x-gateway-admin-token": self.token})
        if response.status_code not in (200, 409):
            raise RolloutError(f"The gateway answered {response.status_code} to {method} {path}; "
                               "check GATEWAY_ADMIN_TOKEN and run this on the gateway's machine.")
        return response

    async def _alone_in(self, name: str) -> set[str]:
        """The pools in which this copy is the only one in rotation."""
        copies = await self.copies()
        mine = _named(copies, name)
        return {pool for pool in mine["pools"] if not any(
            pool in other["pools"] and other["healthy"] and not other["draining"]
            for other in copies if other is not mine)}

    async def _copy(self, name: str) -> Copy:
        return _named(await self.copies(), name)

    async def _until(self, done: Callable[[Copy], bool], name: str, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while not done(await self._copy(name)):
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.2)
        return True

    async def _answers(self, url: str) -> bool:
        deadline = time.monotonic() + self.start_seconds
        while time.monotonic() < deadline:
            try:
                if (await self.client.get(f"{url}/health/live", timeout=5)).status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        return False


def _named(copies: list[Copy], name: str) -> Copy:
    copy = next((copy for copy in copies if copy["name"] == name), None)
    if copy is None:
        raise RolloutError(f"The gateway has no copy named {name}.")
    return copy


def shell(template: str) -> Restart:
    """A restart that runs a command; a non-zero exit stops the rollout."""
    async def restart(copy: Copy) -> None:
        url = str(copy["url"])
        command = template.format(name=copy["name"], url=url, port=urlsplit(url).port or "")
        process = await asyncio.create_subprocess_shell(command)
        if await process.wait():
            raise RolloutError(f"`{command}` exited with {process.returncode}; {copy['name']} stays drained.")
    return restart


async def _main(args: argparse.Namespace) -> None:
    token = os.environ.get("GATEWAY_ADMIN_TOKEN")
    if not token:
        raise RolloutError("Set GATEWAY_ADMIN_TOKEN to the gateway's admin token.")
    async with httpx.AsyncClient(timeout=30) as client:
        rollout = Rollout(client, args.gateway, token, pause_last=args.pause_last,
                          idle_seconds=args.idle_seconds, start_seconds=args.start_seconds)
        names = args.copies.split(",") if args.copies else [str(copy["name"]) for copy in await rollout.copies()]
        await rollout.run(names, shell(args.restart))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--restart", required=True, help="the command that restarts one copy")
    parser.add_argument("--gateway", default="http://127.0.0.1:8000")
    parser.add_argument("--copies", help="comma-separated names, in order (default: every copy, as listed)")
    parser.add_argument("--pause-last", choices=["execution"],
                        help="the pool whose only copy may be restarted; it answers 502 meanwhile")
    parser.add_argument("--idle-seconds", type=float, default=120, help="how long to wait for open requests")
    parser.add_argument("--start-seconds", type=float, default=300, help="how long a copy may take to start")
    try:
        asyncio.run(_main(parser.parse_args()))
    except RolloutError as error:
        sys.exit(f"Rollout stopped: {error}")


if __name__ == "__main__":
    main()
