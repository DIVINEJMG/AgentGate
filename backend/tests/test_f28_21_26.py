from uuid import uuid4

import pytest

from app.application.services.cutover_gate import CutoverReadiness, FinalCutoverGate
from app.application.services.data_migration import (
    LegacyRecord,
    MigrationValidationError,
    RepeatableMigrationPipeline,
)
from app.application.services.shadow_mode import ShadowModeService


def test_repeatable_migration_rejects_duplicate_legacy_identity() -> None:
    record = LegacyRecord(
        source_system="typescript-reference",
        entity_type="worker",
        legacy_id="worker-1",
        payload={"name": "Worker"},
    )
    with pytest.raises(MigrationValidationError, match="Duplicate"):
        RepeatableMigrationPipeline().stage([record, record], batch_id=uuid4())


def test_migration_preserves_identity_mapping_metadata() -> None:
    staged, mappings = RepeatableMigrationPipeline().stage(
        [
            LegacyRecord(
                source_system="typescript-reference",
                entity_type="job",
                legacy_id="job-1",
                payload={"name": "Daily report", "ignored": None},
            )
        ]
    )
    assert staged[0].legacy_id == "job-1"
    assert staged[0].normalized_payload == {"name": "Daily report"}
    assert mappings[0].legacy_id == "job-1"
    assert mappings[0].source_system == "typescript-reference"


@pytest.mark.asyncio
async def test_shadow_mode_never_duplicates_side_effects() -> None:
    async def call():
        return {"ok": True}

    with pytest.raises(PermissionError):
        await ShadowModeService().compare(
            reference_call=call,
            candidate_call=call,
            side_effect=True,
        )


def test_final_runtime_cutover_requires_all_gates() -> None:
    blocked = FinalCutoverGate(
        CutoverReadiness(
            parity_passed=True,
            security_passed=True,
            migration_validated=False,
            rollback_documented=True,
            frontend_verified=True,
        )
    )
    with pytest.raises(RuntimeError, match="cutover gates"):
        blocked.authorize_stage("runtime")


def test_final_runtime_cutover_allows_verified_python_authority() -> None:
    gate = FinalCutoverGate(
        CutoverReadiness(
            parity_passed=True,
            security_passed=True,
            migration_validated=True,
            rollback_documented=True,
            frontend_verified=True,
        )
    )
    assert gate.authorize_stage("runtime").is_authoritative("runtime")
