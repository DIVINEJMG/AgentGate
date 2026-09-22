from dataclasses import dataclass

from sqlalchemy import text

from app.bootstrap.settings import settings
from app.infrastructure.database.session import engine
from app.infrastructure.qstash.provider import UpstashQStashProvider
from app.infrastructure.redis.coordination import RedisCoordinator
from app.infrastructure.storage.upstash_blob_s3 import UpstashBlobS3Transport


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    postgres: str
    redis: str
    object_storage: str
    queue: str

    @property
    def ready(self) -> bool:
        return all(
            value == "ready"
            for value in (
                self.postgres,
                self.redis,
                self.object_storage,
                self.queue,
            )
        )

    def as_dict(self) -> dict[str, str]:
        return {
            "postgres": self.postgres,
            "redis": self.redis,
            "objectStorage": self.object_storage,
            "queue": self.queue,
        }


async def check_readiness() -> DependencyStatus:
    postgres = "unavailable"
    redis = "unavailable"
    object_storage = "unavailable"
    queue = "unavailable"

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        postgres = "ready"
    except Exception:
        postgres = "unavailable"

    coordinator = RedisCoordinator.from_settings()
    try:
        redis = "ready" if await coordinator.ping() else "unavailable"
    except Exception:
        redis = "unavailable"
    finally:
        await coordinator.close()

    if settings.object_storage_provider == "upstash_blob":
        try:
            transport = UpstashBlobS3Transport.from_settings()
            object_storage = "ready" if await transport.healthcheck() else "unavailable"
        except Exception:
            object_storage = "unavailable"
    else:
        object_storage = "unconfigured"

    try:
        queue_provider = UpstashQStashProvider.from_settings()
        queue = "ready" if await queue_provider.healthcheck() else "unavailable"
    except Exception:
        queue = "unconfigured"

    return DependencyStatus(
        postgres=postgres,
        redis=redis,
        object_storage=object_storage,
        queue=queue,
    )
