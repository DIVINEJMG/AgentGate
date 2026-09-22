from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LegacyRecord:
    entity_type: str
    legacy_id: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class NormalizedRecord:
    entity_type: str
    legacy_id: str
    payload: dict[str, object]


class LegacyExtractor(Protocol):
    async def extract(self) -> list[LegacyRecord]: ...


class LegacyNormalizer(Protocol):
    def normalize(self, record: LegacyRecord) -> NormalizedRecord: ...


class MigrationValidator(Protocol):
    def validate(self, record: NormalizedRecord) -> None: ...
