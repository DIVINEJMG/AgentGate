from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import Counter

import httpx
import pytest
from pydantic import ValidationError
from redis.asyncio import Redis

from audoryn_gateway.app import Gateway, _account, _token
from audoryn_gateway.limits import (
    DEFAULT_POLICIES,
    Limiter,
    Penalties,
    Policy,
    PolicyDocument,
    Rule,
    Tier,
    policy_for,
)
from audoryn_gateway.settings import GatewaySettings
from tests.conftest import ADMIN, Server

REDIS = os.environ.get("GATEWAY_TEST_REDIS_URL")
needs_redis = pytest.mark.skipif(not REDIS, reason="GATEWAY_TEST_REDIS_URL is not set (CI provides one)")
CLOSED_REDIS = "redis://127.0.0.1:9/0"
TIGHT = {"general": {"rules": [{"key": "ip", "limit": 3, "window_seconds": 60}], "max_body_bytes": 64},
         "write": {"rules": [{"key": "session", "limit": 2, "window_seconds": 60}], "max_body_bytes": 64}}


def _gateway(cores: list[Server], mode: str, redis: str | None = REDIS, policies: dict | None = None) -> Server:
    """A gateway with its own Redis key space, so test runs never share counters."""
    gateway = Gateway(GatewaySettings(
        core_upstreams=[core.url for core in cores], redis_url=redis, limits_mode=mode,  # type: ignore[arg-type]
        limit_policies=policies if policies is not None else TIGHT, limit_secret="test-secret",  # type: ignore[arg-type]
        admin_token=ADMIN, redis_timeout_seconds=3, health_interval_seconds=0.2,  # type: ignore[arg-type]
        redis_prefix=f"audoryn:gateway-test:{uuid.uuid4().hex[:16]}"))
    return Server(gateway)


async def _statuses(port: int, count: int, method: str = "GET", path: str = "/api/v1/echo",
                    headers: dict[str, str] | None = None, content: bytes | None = None) -> Counter[int]:
    async with httpx.AsyncClient(timeout=30) as client:
        replies = [await client.request(method, f"http://127.0.0.1:{port}{path}", headers=headers, content=content)
                   for _ in range(count)]
    return Counter(reply.status_code for reply in replies)


@pytest.mark.parametrize(("method", "path", "websocket", "group"), [
    ("POST", "/api/v2/auth/login", False, "auth"), ("POST", "/api/v2/auth/signup", False, "auth"),
    ("GET", "/api/v2/auth/session", False, "general"),
    ("POST", "/api/v2/organizations/x/conversations/y/attachments", False, "upload"),
    ("PATCH", "/api/v2/organizations/x/workers/y", False, "write"), ("GET", "/api/v1/x", False, "general"),
    ("POST", "/internal/v1/runtime/execute", False, "internal"), ("POST", "/integration-events/github", False, "internal"),
    ("GET", "/health/ready", False, None), ("GET", "/ws/v1/organizations/x", True, "realtime"),
])
def test_route_groups(method: str, path: str, websocket: bool, group: str | None):
    assert policy_for(method, path, websocket) == group


def test_every_route_group_has_a_default_policy():
    assert {"auth", "upload", "write", "general", "realtime", "internal"} <= set(DEFAULT_POLICIES)


def test_session_token_comes_from_the_bearer_header_or_the_websocket_query():
    assert _token({"headers": [(b"authorization", b"Bearer abc")], "query_string": b""}) == "abc"
    assert _token({"headers": [], "query_string": b"x=1&access_token=def"}) == "def"
    assert _token({"headers": [(b"authorization", b"Basic zzz")], "query_string": b""}) is None


def test_session_fingerprints_never_contain_the_token():
    limiter = Limiter(None, b"secret", DEFAULT_POLICIES, "enforce")
    assert "my-token" not in limiter.fingerprint("my-token")
    assert limiter.fingerprint("my-token") == limiter.fingerprint("my-token") != limiter.fingerprint("other")


def test_without_redis_the_limits_are_off():
    assert Limiter(None, b"s", DEFAULT_POLICIES, "enforce").mode_of("general") == "off"


async def test_policy_override_replaces_only_that_group():
    settings = GatewaySettings(limit_policies={"general": Policy(rules=[Rule(key="ip", limit=1, window_seconds=1)])})
    assert settings.policies["general"].rules[0].limit == 1
    assert settings.policies["write"] == DEFAULT_POLICIES["write"]


@needs_redis
async def test_report_mode_counts_but_refuses_nothing(cores: list[Server]):
    server = _gateway(cores, "report")
    try:
        assert await _statuses(server.port, 6) == Counter({200: 6})
    finally:
        server.stop()


@needs_redis
async def test_enforce_mode_refuses_over_the_limit_with_retry_after(cores: list[Server]):
    server = _gateway(cores, "enforce")
    try:
        assert await _statuses(server.port, 5) == Counter({200: 3, 429: 2})
        async with httpx.AsyncClient(timeout=30) as client:
            refused = await client.get(f"http://127.0.0.1:{server.port}/api/v1/echo")
        assert refused.json() == {"detail": "Too many requests. Try again shortly."}
        assert 1 <= int(refused.headers["retry-after"]) <= 60
        assert (await _statuses(server.port, 2, path="/health/live")) == Counter({200: 2})  # never limited
    finally:
        server.stop()


@needs_redis
async def test_session_limits_count_each_session_apart(cores: list[Server]):
    server = _gateway(cores, "enforce")
    try:
        first = await _statuses(server.port, 3, "POST", headers={"authorization": "Bearer one"})
        second = await _statuses(server.port, 2, "POST", headers={"authorization": "Bearer two"})
        assert first == Counter({200: 2, 429: 1})
        assert second == Counter({200: 2})
    finally:
        server.stop()


@needs_redis
async def test_bodies_over_the_cap_are_refused_only_when_enforced(cores: list[Server]):
    for mode, expected in (("report", 200), ("enforce", 413)):
        server = _gateway(cores, mode)
        try:
            assert await _statuses(server.port, 1, "POST", content=b"x" * 65) == Counter({expected: 1})
        finally:
            server.stop()


@needs_redis
async def test_a_drain_reaches_every_gateway_copy_through_redis(cores: list[Server]):
    first = _gateway(cores, "off", policies={})
    second = Server(Gateway(first.app.settings))  # same settings, so the same key prefix
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            drained = await client.post(f"http://127.0.0.1:{first.port}/gateway/admin/copies/core-0/drain",
                                        headers={"x-gateway-admin-token": ADMIN})
            assert drained.status_code == 200
            for _ in range(50):  # the second gateway's own checks read the drain from Redis
                listed = await client.get(f"http://127.0.0.1:{second.port}/gateway/admin/copies",
                                          headers={"x-gateway-admin-token": ADMIN})
                if {copy["name"]: copy["draining"] for copy in listed.json()["copies"]}["core-0"]:
                    break
                await asyncio.sleep(0.2)
            else:
                pytest.fail("the drain never reached the second gateway")
            await client.post(f"http://127.0.0.1:{first.port}/gateway/admin/copies/core-0/restore",
                              headers={"x-gateway-admin-token": ADMIN})
    finally:
        first.stop()
        second.stop()


async def test_redis_down_refuses_sign_in_but_lets_everything_else_through(cores: list[Server]):
    server = _gateway(cores, "enforce", redis=CLOSED_REDIS, policies={})
    try:
        assert await _statuses(server.port, 2) == Counter({200: 2})
        refused = await _statuses(server.port, 1, "POST", "/api/v2/auth/login")
        assert refused == Counter({503: 1})
    finally:
        server.stop()


async def test_report_mode_neither_refuses_nor_waits_for_redis(cores: list[Server]):
    server = _gateway(cores, "report", redis=CLOSED_REDIS, policies={})  # Redis times out after 3 s
    try:
        started = time.perf_counter()
        assert await _statuses(server.port, 1, "POST", "/api/v2/auth/login") == Counter({404: 1})  # reached the core
        assert time.perf_counter() - started < 1.5  # counted alongside, not before
    finally:
        server.stop()


def test_redis_client_is_created_only_when_configured():
    assert Gateway(GatewaySettings()).redis is None
    assert isinstance(Gateway(GatewaySettings(redis_url=CLOSED_REDIS)).redis, Redis)  # type: ignore[arg-type]


async def _limiter_on_redis() -> tuple[Limiter, Redis]:
    client = Redis.from_url(REDIS or "", socket_timeout=3, socket_connect_timeout=3, decode_responses=True)
    limiter = Limiter(client, b"s", DEFAULT_POLICIES, "off", prefix=f"audoryn:gateway-test:{uuid.uuid4().hex[:16]}")
    return limiter, client


@needs_redis
async def test_console_policies_are_followed_and_a_bad_document_never_applied():
    limiter, client = await _limiter_on_redis()
    key = f"{limiter._prefix}:policies"
    try:
        await limiter.refresh()  # nothing set in the Console: the settings stay
        assert limiter.console_version is None and limiter.mode == "off"

        await client.set(key, '{"version":2,"mode":"report","policies":{"general":'
                              '{"rules":[{"key":"ip","limit":5,"window_seconds":60}]}}}', ex=60)
        await limiter.refresh()
        assert limiter.console_version == 2 and limiter.mode == "report"
        assert limiter.policies["general"].rules[0].limit == 5
        assert limiter.policies["write"] == DEFAULT_POLICIES["write"]  # groups not named keep the settings

        await client.set(key, '{"version":3,"mode":"loud","policies":{}}', ex=60)  # invalid
        await limiter.refresh()
        assert limiter.console_version == 2 and limiter.policies["general"].rules[0].limit == 5

        await client.delete(key)  # gone (a flushed Redis): the last good stay until the Console resends
        await limiter.refresh()
        assert limiter.console_version == 2

        await client.set(key, '{"version":4,"mode":null,"policies":{}}', ex=60)
        await limiter.refresh()
        assert limiter.console_version == 4 and limiter.mode == "off"  # back to the settings
        assert limiter.policies["general"] == DEFAULT_POLICIES["general"]
    finally:
        await client.delete(key)
        await client.aclose()


async def test_without_redis_the_console_document_is_not_read():
    limiter = Limiter(None, b"s", DEFAULT_POLICIES, "report")
    await limiter.refresh()
    assert limiter.console_version is None and limiter.mode == "report"


def test_ip_rules_never_escalate():
    with pytest.raises(ValidationError, match="never apply to ip"):
        Rule(key="ip", limit=1, window_seconds=60, penalties=Penalties(tiers=[Tier(block_seconds=60)]))


def test_the_default_sign_in_ladder_counts_accounts_not_addresses():
    ip, account = DEFAULT_POLICIES["auth"].rules
    assert (ip.key, ip.limit, ip.penalties) == ("ip", 100, None)
    assert (account.key, account.limit, account.window_seconds) == ("account", 15, 300)
    assert account.penalties is not None
    assert [(tier.block_seconds, tier.limit) for tier in account.penalties.tiers] == [
        (300, 10), (7200, 10), (86_400, None)]


def test_a_console_document_can_set_penalties():
    document = PolicyDocument.model_validate_json(
        '{"version":1,"mode":null,"policies":{"auth":{"rules":[{"key":"account","limit":15,"window_seconds":300,'
        '"penalties":{"tiers":[{"block_seconds":300,"limit":10},{"block_seconds":86400}],'
        '"reset_after_seconds":86400}}]}}}')
    assert document.policies["auth"].rules[0].penalties.tiers[0].limit == 10  # type: ignore[union-attr]


@pytest.mark.parametrize(("body", "account"), [
    (b'{"email": " User@Example.com ", "password": "x"}', "user@example.com"),
    (b'{"password": "x"}', None), (b'{"email": 5}', None), (b"[1]", None), (b"not json", None), (bytes([255]), None),
])
def test_the_account_is_the_email_a_body_names(body: bytes, account: str | None):
    assert _account(body) == account


@needs_redis
async def test_penalties_block_longer_each_time_then_start_over():
    client = Redis.from_url(REDIS or "", socket_timeout=3, socket_connect_timeout=3)
    ladder = Penalties(tiers=[Tier(block_seconds=1, limit=1), Tier(block_seconds=2)], reset_after_seconds=60)
    policies = {**DEFAULT_POLICIES, "write": Policy(rules=[Rule(key="session", limit=2, window_seconds=3600,
                                                                penalties=ladder)])}
    limiter = Limiter(client, b"s", policies, "enforce", prefix=f"audoryn:gateway-test:{uuid.uuid4().hex[:16]}")

    async def tries(count: int, token: str = "one") -> list[int]:
        """200 when let through, else the seconds the block has left."""
        decisions = [await limiter.check("write", ip="10.0.0.1", token=token, path="/x") for _ in range(count)]
        return [decision.retry_after() if decision.refuse else 200 for decision in decisions]

    try:
        assert await tries(4) == [200, 200, 1, 1]  # over the limit: blocked 1 s, and still blocked
        assert await tries(1, token="two") == [200]  # another session is not
        await asyncio.sleep(1.1)
        assert await tries(2) == [200, 2]  # the first tier's stricter limit (1), then the last block: 2 s
        await asyncio.sleep(2.1)
        assert await tries(3) == [200, 200, 1]  # after the last tier, the ladder starts over
    finally:
        await client.aclose()


@needs_redis
async def test_sign_in_attempts_count_per_account_and_address(cores: list[Server]):
    ladder = {"tiers": [{"block_seconds": 60}]}
    server = _gateway(cores, "enforce", policies={"auth": {"rules": [
        {"key": "ip", "limit": 100, "window_seconds": 300},
        {"key": "account", "limit": 2, "window_seconds": 300, "penalties": ladder}]}})

    async def sign_in(email: str) -> int:
        async with httpx.AsyncClient(timeout=30) as client:
            reply = await client.post(f"http://127.0.0.1:{server.port}/api/v2/auth/login",
                                      json={"email": email, "password": "x"})
        return reply.status_code

    try:
        assert [await sign_in("a@example.com") for _ in range(3)] == [404, 404, 429]  # 404: the stand-in core
        assert await sign_in(" A@Example.com") == 429  # the same account, as the core normalises it
        assert await sign_in("b@example.com") == 404  # another account from the same address is not blocked
    finally:
        server.stop()
