from uuid import uuid4

import pytest

from app.infrastructure.auth.passwords import hash_password, verify_password
from app.infrastructure.auth.session_store import RedisSessionStore


class FakeRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.expiries: dict[str, int] = {}
        self.counters: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.expiries[key] = ex
        return True

    async def get(self, key):
        return self.values.get(key)

    async def delete(self, key):
        self.values.pop(key, None)
        self.counters.pop(key, None)
        return 1

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key, seconds):
        self.expiries[key] = seconds
        return True


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("very-secure-password")
    second = hash_password("very-secure-password")
    assert first != second
    assert verify_password("very-secure-password", first)
    assert not verify_password("wrong-password", first)


def test_password_minimum_length() -> None:
    with pytest.raises(ValueError):
        hash_password("short")


@pytest.mark.asyncio
async def test_session_round_trip_and_revocation() -> None:
    redis = FakeRedis()
    store = RedisSessionStore(redis, ttl_seconds=3600)  # type: ignore[arg-type]
    user_id = uuid4()
    token = await store.create(user_id)
    record = await store.get(token)
    assert record is not None
    assert record.user_id == user_id
    await store.revoke(token)
    assert await store.get(token) is None


@pytest.mark.asyncio
async def test_failed_login_counter_is_bounded_in_redis() -> None:
    redis = FakeRedis()
    store = RedisSessionStore(redis, ttl_seconds=3600)  # type: ignore[arg-type]
    assert await store.record_failed_login("user@example.com") == 1
    assert await store.record_failed_login("user@example.com") == 2
    await store.clear_failed_login("user@example.com")
    assert await store.record_failed_login("user@example.com") == 1
