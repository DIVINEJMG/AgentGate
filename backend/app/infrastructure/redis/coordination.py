import secrets
from dataclasses import dataclass
from redis.asyncio import Redis
from app.bootstrap.settings import settings

@dataclass(frozen=True, slots=True)
class Lease:
    key: str
    token: str

class RedisCoordinator:
    """Ephemeral coordination only. Canonical business state must remain in PostgreSQL."""
    def __init__(self, client: Redis) -> None:
        self._client = client

    @classmethod
    def from_settings(cls) -> "RedisCoordinator":
        return cls(Redis.from_url(settings.redis_url, decode_responses=True))

    async def ping(self) -> bool:
        return bool(await self._client.ping())

    async def acquire_lock(self, key: str, *, ttl_seconds: int = 30) -> Lease | None:
        token = secrets.token_urlsafe(24)
        acquired = await self._client.set(f"lock:{key}", token, ex=ttl_seconds, nx=True)
        return Lease(key=key, token=token) if acquired else None

    async def release_lock(self, lease: Lease) -> bool:
        script = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
        return bool(await self._client.eval(script, 1, f"lock:{lease.key}", lease.token))

    async def heartbeat(self, key: str, value: str, *, ttl_seconds: int = 60) -> None:
        await self._client.set(f"heartbeat:{key}", value, ex=ttl_seconds)

    async def cache_set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        await self._client.set(f"cache:{key}", value, ex=ttl_seconds)

    async def cache_get(self, key: str) -> str | None:
        return await self._client.get(f"cache:{key}")

    async def publish(self, channel: str, payload: str) -> int:
        return int(await self._client.publish(f"audoryn:{channel}", payload))

    async def close(self) -> None:
        await self._client.aclose()
