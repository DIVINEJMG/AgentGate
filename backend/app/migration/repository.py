from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.migration.service import PreparedMigrationRecord
from app.migration.tables import (
    migration_batches,
    migration_id_mappings,
    migration_staging_records,
)


class MigrationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_batch(self, *, source_system: str) -> UUID:
        batch_id = uuid4()
        await self._session.execute(
            insert(migration_batches).values(
                id=batch_id,
                source_system=source_system,
                status="staging",
                created_at=datetime.now(UTC),
            )
        )
        return batch_id

    async def stage(
        self,
        *,
        batch_id: UUID,
        records: list[PreparedMigrationRecord],
    ) -> None:
        now = datetime.now(UTC)
        for record in records:
            await self._session.execute(
                insert(migration_staging_records).values(
                    id=uuid4(),
                    batch_id=batch_id,
                    entity_type=record.entity_type,
                    legacy_id=record.legacy_id,
                    new_id=record.new_id,
                    payload=record.payload,
                    validation_status="validated",
                    created_at=now,
                )
            )
            await self._session.execute(
                insert(migration_id_mappings).values(
                    id=uuid4(),
                    batch_id=batch_id,
                    entity_type=record.entity_type,
                    legacy_id=record.legacy_id,
                    new_id=record.new_id,
                    source_system=record.source_system,
                    migrated_at=now,
                )
            )
