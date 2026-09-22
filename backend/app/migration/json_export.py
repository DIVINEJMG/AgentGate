import json
from pathlib import Path

from app.migration.contracts import LegacyRecord


class JsonDirectoryExtractor:
    """Reads user-approved legacy JSON exports only; never reaches AppDeploy directly."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    async def extract(self) -> list[LegacyRecord]:
        records: list[LegacyRecord] = []
        for path in sorted(self._directory.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise TypeError(f"{path.name} must contain a JSON array.")
            entity_type = path.stem
            for item in raw:
                if not isinstance(item, dict):
                    raise TypeError(f"{path.name} contains a non-object record.")
                legacy_id = str(item.get("id") or "").strip()
                if not legacy_id:
                    raise ValueError(f"{path.name} contains a record without id.")
                records.append(
                    LegacyRecord(
                        entity_type=entity_type,
                        legacy_id=legacy_id,
                        payload=dict(item),
                    )
                )
        return records
