from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

from app.bootstrap.settings import settings


@dataclass(frozen=True, slots=True)
class SessionRecord:
    user_id: UUID


class RedisSessionStore:
    def __init__(self, redis: Redis, *, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl_seconds = ttl_seconds

    @classmethod
    def from_settings(cls) -> RedisSessionStore:
        return cls(
            Redis.from_url(settings.redis_dsn, decode_responses=True),
            ttl_seconds=settings.auth_session_ttl_seconds,
        )

    @staticmethod
    def _key(token: str) -> str:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"auth:session:{digest}"

    async def create(self, user_id: UUID) -> str:
        token = secrets.token_urlsafe(32)
        payload = json.dumps({"user_id": str(user_id)}, separators=(",", ":"))
        await self._redis.set(self._key(token), payload, ex=self._ttl_seconds)
        return token

    async def get(self, token: str) -> SessionRecord | None:
        raw = await self._redis.get(self._key(token))
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            return SessionRecord(user_id=UUID(str(payload["user_id"])))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            await self._redis.delete(self._key(token))
            return None

    async def revoke(self, token: str) -> None:
        await self._redis.delete(self._key(token))

    async def record_failed_login(self, email: str) -> int:
        key = f"auth:failed:{hashlib.sha256(email.encode('utf-8')).hexdigest()}"
        count = int(await self._redis.incr(key))
        if count == 1:
            await self._redis.expire(key, settings.auth_login_failure_window_seconds)
        return count

    async def clear_failed_login(self, email: str) -> None:
        key = f"auth:failed:{hashlib.sha256(email.encode('utf-8')).hexdigest()}"
        await self._redis.delete(key)
