import json

import pytest

from app.migration.json_export import JsonDirectoryExtractor
from app.migration.normalize import CanonicalMigrationValidator, CanonicalNormalizer
from app.migration.service import MigrationPreparationService


@pytest.mark.asyncio
async def test_approved_json_export_is_normalized_with_stable_legacy_identity(tmp_path) -> None:
    (tmp_path / "organizations.json").write_text(
        json.dumps([{"id": "legacy-org-1", "name": "Acme"}]),
        encoding="utf-8",
    )
    service = MigrationPreparationService(
        extractor=JsonDirectoryExtractor(tmp_path),
        normalizer=CanonicalNormalizer(),
        validator=CanonicalMigrationValidator(),
        source_system="approved_export",
    )
    records = await service.prepare()
    assert len(records) == 1
    assert records[0].legacy_id == "legacy-org-1"
    assert records[0].source_system == "approved_export"
    assert records[0].payload["name"] == "Acme"


@pytest.mark.asyncio
async def test_duplicate_legacy_identity_fails_before_database_write(tmp_path) -> None:
    (tmp_path / "organizations.json").write_text(
        json.dumps(
            [
                {"id": "legacy-org-1", "name": "Acme"},
                {"id": "legacy-org-1", "name": "Acme duplicate"},
            ]
        ),
        encoding="utf-8",
    )
    service = MigrationPreparationService(
        extractor=JsonDirectoryExtractor(tmp_path),
        normalizer=CanonicalNormalizer(),
        validator=CanonicalMigrationValidator(),
    )
    with pytest.raises(ValueError, match="Duplicate legacy identity"):
        await service.prepare()
