from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class LegacyRecord:
    source_system: str
    entity_type: str
    legacy_id: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class StagedRecord:
    batch_id: UUID
    source_system: str
    entity_type: str
    legacy_id: str
    normalized_payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class IdentityMapping:
    legacy_id: str
    new_id: UUID
    source_system: str
    migrated_at: datetime


class MigrationValidationError(ValueError):
    pass


class RepeatableMigrationPipeline:
    """Pure migration pipeline: extract -> normalize -> validate -> stage -> map.

    Canonical promotion is deliberately outside this class and requires a separate,
    explicitly enabled production operation.
    """

    SUPPORTED_ENTITY_TYPES = frozenset(
        {
            "organization",
            "membership",
            "agent_identity",
            "worker",
            "job",
            "result",
        }
    )

    def stage(
        self,
        records: Iterable[LegacyRecord],
        *,
        batch_id: UUID | None = None,
    ) -> tuple[list[StagedRecord], list[IdentityMapping]]:
        batch = batch_id or uuid4()
        seen: set[tuple[str, str, str]] = set()
        staged: list[StagedRecord] = []
        mappings: list[IdentityMapping] = []

        for record in records:
            if record.entity_type not in self.SUPPORTED_ENTITY_TYPES:
                raise MigrationValidationError(f"Unsupported entity type: {record.entity_type}")
            if not record.legacy_id.strip():
                raise MigrationValidationError("legacy_id is required.")

            key = (record.source_system, record.entity_type, record.legacy_id)
            if key in seen:
                raise MigrationValidationError(
                    f"Duplicate legacy identity: {record.source_system}:{record.entity_type}:{record.legacy_id}"
                )
            seen.add(key)

            normalized = {str(k): v for k, v in record.payload.items() if v is not None}
            staged.append(
                StagedRecord(
                    batch_id=batch,
                    source_system=record.source_system,
                    entity_type=record.entity_type,
                    legacy_id=record.legacy_id,
                    normalized_payload=normalized,
                )
            )
            mappings.append(
                IdentityMapping(
                    legacy_id=record.legacy_id,
                    new_id=uuid4(),
                    source_system=record.source_system,
                    migrated_at=datetime.now(UTC),
                )
            )

        return staged, mappings
