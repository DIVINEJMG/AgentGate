import secrets
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.bootstrap.settings import settings


@dataclass(frozen=True, slots=True)
class Lease:
    key: str
    token: str


class RedisCoordinator:
    """Ephemeral coordination only. Canonical business state must remain in PostgreSQL."""

    def __init__(self, client: Redis) -> None:
        self._client: Any = client

    @classmethod
    def from_settings(cls) -> "RedisCoordinator":
        return cls(Redis.from_url(settings.redis_dsn, decode_responses=True))

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def acquire_lock(self, key: str, *, ttl_seconds: int = 30) -> Lease | None:
        token = secrets.token_urlsafe(24)
        acquired = await self._client.set(f"lock:{key}", token, ex=ttl_seconds, nx=True)
        return Lease(key=key, token=token) if acquired else None

    async def active_locks(self, keys: list[str]) -> dict[str, bool]:
        """Observe execution ownership without exposing tokens or extending leases."""
        if not keys:
            return {}
        values = await self._client.mget([f"lock:{key}" for key in keys])
        return {key: value is not None for key, value in zip(keys, values, strict=True)}

    async def release_lock(self, lease: Lease) -> bool:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) end return 0"
        )
        return bool(await self._client.eval(script, 1, f"lock:{lease.key}", lease.token))

    async def renew_lock(self, lease: Lease, *, ttl_seconds: int) -> bool:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('expire', KEYS[1], ARGV[2]) end return 0"
        )
        return bool(
            await self._client.eval(
                script,
                1,
                f"lock:{lease.key}",
                lease.token,
                max(1, ttl_seconds),
            )
        )

    async def next_fencing_token(self, key: str) -> int:
        """Advance the owner generation after acquiring the corresponding lock."""
        return int(await self._client.incr(f"fence:{key}"))

    async def current_fencing_token(self, key: str) -> int:
        return int(await self._client.get(f"fence:{key}") or 0)

    async def heartbeat(self, key: str, value: str, *, ttl_seconds: int = 60) -> None:
        await self._client.set(f"heartbeat:{key}", value, ex=ttl_seconds)

    async def cache_set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        await self._client.set(f"cache:{key}", value, ex=ttl_seconds)

    async def cache_get(self, key: str) -> str | None:
        value = await self._client.get(f"cache:{key}")
        return value if isinstance(value, str) else None

    async def cache_delete(self, key: str) -> None:
        await self._client.delete(f"cache:{key}")

    async def increment_counter(self, key: str, *, ttl_seconds: int) -> int:
        script = "local n=redis.call('INCR',KEYS[1]); redis.call('EXPIRE',KEYS[1],ARGV[1]); return n"
        return int(await self._client.eval(script, 1, f"cache:{key}", max(1, ttl_seconds)))

    async def reserve_units(self, key: str, *, units: int, limit: int, ttl_seconds: int) -> bool:
        """Reserve a conservative request/token budget without oversubscribing it."""
        script = (
            "local n=tonumber(redis.call('GET',KEYS[1]) or '0'); "
            "local units=tonumber(ARGV[1]); if n+units>tonumber(ARGV[2]) then return 0 end; "
            "redis.call('INCRBY',KEYS[1],units); "
            "if n==0 then redis.call('EXPIRE',KEYS[1],ARGV[3]) end; return 1"
        )
        return bool(await self._client.eval(script, 1, f"limit:{key}",
            max(1, units), max(1, limit), max(1, ttl_seconds)))

    async def reserve_limit(self, key: str, *, limit: int, ttl_seconds: int) -> bool:
        """Atomically reserve one unit of an ephemeral provider budget."""
        script = (
            "local n=redis.call('incr',KEYS[1]); "
            "if n==1 then redis.call('expire',KEYS[1],ARGV[2]) end; "
            "return n<=tonumber(ARGV[1])"
        )
        return bool(await self._client.eval(
            script, 1, f"limit:{key}", max(1, limit), max(1, ttl_seconds)
        ))

    async def reserve_ai_budget(self, identity: str, entries: list[dict]) -> int:
        """Reserve every workload ceiling atomically; return the rejected entry index."""
        script = (
            "if redis.call('EXISTS',KEYS[1])==1 then return -1 end; "
            "for i=2,#KEYS do local p=(i-2)*3; "
            "if tonumber(redis.call('GET',KEYS[i]) or '0')+tonumber(ARGV[p+1])"
            ">tonumber(ARGV[p+2]) then return i-2 end end; "
            "for i=2,#KEYS do local p=(i-2)*3; local exists=redis.call('EXISTS',KEYS[i]); "
            "redis.call('INCRBY',KEYS[i],ARGV[p+1]); "
            "if exists==0 then redis.call('EXPIRE',KEYS[i],ARGV[p+3]) end end; "
            "redis.call('SET',KEYS[1],'reserved','EX',86400); return -1"
        )
        args = [value for entry in entries for value in (
            entry["units"], entry["limit"], entry["ttl"])]
        return int(await self._client.eval(script, len(entries) + 1,
            f"ai-reservation:{identity}", *[f"limit:{e['key']}" for e in entries], *args))

    async def settle_ai_budget(self, identity: str, entries: list[dict], *,
                               actual_tokens: int, called: bool) -> bool:
        """Apply usage/refund once, preserving TTL and never recreating expired counters."""
        script = (
            "if redis.call('GET',KEYS[1])~='reserved' then return 0 end; "
            "for i=2,#KEYS do if redis.call('EXISTS',KEYS[i])==1 then "
            "local p=(i-2)*2; local n=tonumber(redis.call('GET',KEYS[i])); "
            "local next=math.max(0,n-tonumber(ARGV[p+1])+tonumber(ARGV[p+2])); "
            "redis.call('SET',KEYS[i],next,'KEEPTTL') end end; "
            "redis.call('SET',KEYS[1],'settled','KEEPTTL'); return 1"
        )
        args = [value for entry in entries for value in (
            entry["units"], actual_tokens if entry["kind"] == "tokens" else int(called))]
        return bool(await self._client.eval(script, len(entries) + 1,
            f"ai-reservation:{identity}", *[f"limit:{e['key']}" for e in entries], *args))

    async def acquire_slot(self, key: str, *, limit: int, ttl_seconds: int = 120) -> Lease | None:
        token = secrets.token_urlsafe(24)
        script = (
            "local t=redis.call('TIME'); local now=tonumber(t[1]); "
            "redis.call('ZREMRANGEBYSCORE',KEYS[1],'-inf',now); "
            "if redis.call('ZCARD',KEYS[1])>=tonumber(ARGV[2]) then return 0 end; "
            "redis.call('ZADD',KEYS[1],now+tonumber(ARGV[3]),ARGV[1]); "
            "redis.call('EXPIRE',KEYS[1],ARGV[3]); return 1"
        )
        ok = await self._client.eval(script, 1, f"slots:{key}", token, limit, ttl_seconds)
        return Lease(key=key, token=token) if ok else None

    async def release_slot(self, lease: Lease) -> None:
        await self._client.zrem(f"slots:{lease.key}", lease.token)

    async def publish(self, channel: str, payload: str) -> int:
        return int(await self._client.publish(f"audoryn:{channel}", payload))

    async def close(self) -> None:
        await self._client.aclose()
