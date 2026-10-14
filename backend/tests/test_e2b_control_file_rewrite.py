"""The sandbox's sticky, protected /tmp lets its file service create a file but not reopen one that
exists (fs.protected_regular), so a second write of the same request file was refused and every
edit after the first failed. The runtime now removes such a file and writes it again."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bootstrap.settings import settings
from app.execution.coding.e2b import E2BCodingRuntime


class Files:
    def __init__(self, error: str = "500: error opening file: open /tmp/audoryn-step.json: permission denied"):
        self.stored: dict[str, str] = {}
        self.calls: list[str] = []
        self.error = error

    async def write(self, path: str, content: str) -> None:
        self.calls.append("write")
        if path in self.stored:
            raise RuntimeError(self.error)
        self.stored[path] = content

    async def remove(self, path: str) -> None:
        self.calls.append("remove")
        self.stored.pop(path, None)


def _runtime(files: Files, monkeypatch: pytest.MonkeyPatch) -> E2BCodingRuntime:
    monkeypatch.setattr(settings, "coding_execution_enabled", True)
    monkeypatch.setattr(settings, "e2b_api_key", SimpleNamespace(get_secret_value=lambda: "test"))
    monkeypatch.setattr(settings, "coding_template_id", "template")
    runtime = E2BCodingRuntime(sdk=object())
    runtime._sandboxes["sb"] = SimpleNamespace(files=files)
    return runtime


@pytest.mark.asyncio
async def test_the_same_request_file_can_be_written_again(monkeypatch: pytest.MonkeyPatch) -> None:
    files = Files()
    runtime = _runtime(files, monkeypatch)
    for content in ("one", "two", "three"):
        await runtime.write("sb", "/tmp/audoryn-step.json", content)
    assert files.stored["/tmp/audoryn-step.json"] == "three"
    # A new file is one call; only a refused rewrite removes and writes again.
    assert files.calls == ["write", "write", "remove", "write", "write", "remove", "write"]


@pytest.mark.asyncio
async def test_other_write_errors_are_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    files = Files(error="502: sandbox unavailable")
    runtime = _runtime(files, monkeypatch)
    await runtime.write("sb", "/tmp/a", "one")
    with pytest.raises(RuntimeError, match="unavailable"):
        await runtime.write("sb", "/tmp/a", "two")
    assert "remove" not in files.calls
