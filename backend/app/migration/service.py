from dataclasses import dataclass
from uuid import UUID, uuid4

from app.migration.contracts import LegacyExtractor, LegacyNormalizer, MigrationValidator


@dataclass(frozen=True, slots=True)
class PreparedMigrationRecord:
    entity_type: str
    legacy_id: str
    new_id: UUID
    source_system: str
    payload: dict[str, object]


class MigrationPreparationService:
    def __init__(
        self,
        *,
        extractor: LegacyExtractor,
        normalizer: LegacyNormalizer,
        validator: MigrationValidator,
        source_system: str = "legacy_export",
    ) -> None:
        self._extractor = extractor
        self._normalizer = normalizer
        self._validator = validator
        self._source_system = source_system

    async def prepare(self) -> list[PreparedMigrationRecord]:
        legacy_records = await self._extractor.extract()
        seen: set[tuple[str, str]] = set()
        prepared: list[PreparedMigrationRecord] = []
        for legacy in legacy_records:
            normalized = self._normalizer.normalize(legacy)
            self._validator.validate(normalized)
            key = (normalized.entity_type, normalized.legacy_id)
            if key in seen:
                raise ValueError(f"Duplicate legacy identity: {key[0]}:{key[1]}")
            seen.add(key)
            prepared.append(
                PreparedMigrationRecord(
                    entity_type=normalized.entity_type,
                    legacy_id=normalized.legacy_id,
                    new_id=uuid4(),
                    source_system=self._source_system,
                    payload=normalized.payload,
                )
            )
        return prepared
