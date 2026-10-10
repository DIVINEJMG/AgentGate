import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.execution.coding.e2b import E2BCodingRuntime
from app.execution.coding.service import log_failure


@pytest.mark.parametrize("initial,ack_lost,remote_match,expected", [
    (True, False, True, "reused"), (False, False, True, "uploaded"),
    (False, True, True, "reconciled"), (False, True, False, None),
])
async def test_manifest_reconciliation_requires_remote_hash(initial, ack_lost, remote_match, expected):
    runtime = object.__new__(E2BCodingRuntime)
    content = '{"file.py": {"size": 1}}'
    digest = hashlib.sha256(content.encode()).hexdigest()
    response = lambda value: SimpleNamespace(exit_code=0, stdout=json.dumps({"sha256": digest if value else None}))
    runtime.run = AsyncMock(side_effect=[response(initial), response(remote_match)])
    error = RuntimeError("Expected to receive information about written file")
    runtime.write = AsyncMock(side_effect=error if ack_lost else None)
    stages = []
    if expected:
        assert await runtime.ensure_control_file("same-sandbox", "/tmp/audoryn-baseline-manifest.json", content, stages.append) == expected
        assert runtime.write.await_count == (0 if initial else 1)
    else:
        with pytest.raises(RuntimeError) as caught:
            await runtime.ensure_control_file("same-sandbox", "/tmp/audoryn-baseline-manifest.json", content, stages.append)
        assert caught.value is error
        assert stages[-1] == "baseline_manifest_upload"


async def test_control_reconciliation_cannot_overwrite_source():
    runtime = object.__new__(E2BCodingRuntime)
    runtime.write = AsyncMock()
    with pytest.raises(ValueError, match="Unsupported"):
        await runtime.ensure_control_file("sandbox", "/workspace/source.py", "content", lambda _: None)
    runtime.write.assert_not_awaited()


async def test_unsafe_remote_manifest_blocks_upload():
    runtime = object.__new__(E2BCodingRuntime)
    runtime.run = AsyncMock(return_value=SimpleNamespace(exit_code=1, stdout=""))
    runtime.write = AsyncMock()
    with pytest.raises(ValueError, match="inspected safely"):
        await runtime.ensure_control_file("sandbox", "/tmp/audoryn-baseline-manifest.json", "{}", lambda _: None)
    runtime.write.assert_not_awaited()


async def test_successful_ack_with_wrong_remote_hash_is_rejected():
    runtime = object.__new__(E2BCodingRuntime)
    runtime.run = AsyncMock(return_value=SimpleNamespace(exit_code=0, stdout='{"sha256": "wrong"}'))
    runtime.write = AsyncMock()
    with pytest.raises(ValueError, match="checksum did not match"):
        await runtime.ensure_control_file("sandbox", "/tmp/audoryn-baseline-manifest.json", "{}", lambda _: None)


def test_diagnostics_allow_only_known_sdk_reasons(caplog):
    error = RuntimeError("Expected to receive information about written file")
    with caplog.at_level("WARNING", logger="uvicorn.error"):
        log_failure(SimpleNamespace(operation="repository.workspace.diff"), error)
        log_failure(SimpleNamespace(), RuntimeError("SECRET-fixture-token"))
    assert "missing_write_acknowledgement" in caplog.text
    assert "SECRET-fixture-token" not in caplog.text
