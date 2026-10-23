"""Where the backend's code lives (architecture plan, Phase 4), for the verify and audit scripts.

The old backend/app is three packages now: the core (backend/app), the shared code
(packages/shared/audoryn_shared) and the execution service (services/execution/audoryn_execution).
`Code("execution", "providers", "browser.py")` is that file in whichever package holds it, and
`Code("domain").rglob("*.py")` walks that folder in all three, so a check still covers all the code.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    ROOT / "backend" / "app",
    ROOT / "packages" / "shared" / "audoryn_shared",
    ROOT / "services" / "execution" / "audoryn_execution",
)


class Code:
    def __init__(self, *parts: str) -> None:
        self.parts = parts

    def __truediv__(self, part: str) -> Code:
        return Code(*self.parts, *Path(part).parts)

    def __str__(self) -> str:
        return "/".join(("app", *self.parts))

    def _found(self) -> list[Path]:
        return [root.joinpath(*self.parts) for root in PACKAGES if root.joinpath(*self.parts).exists()]

    def path(self) -> Path:
        found = self._found()
        if not found:
            raise FileNotFoundError(f"{self} is in none of {[str(p.relative_to(ROOT)) for p in PACKAGES]}")
        return found[0]

    def exists(self) -> bool:
        return bool(self._found())

    def is_dir(self) -> bool:
        return any(path.is_dir() for path in self._found())

    def is_file(self) -> bool:
        return any(path.is_file() for path in self._found())

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.path().read_text(encoding=encoding)

    def rglob(self, pattern: str) -> Iterator[Path]:
        for path in self._found():
            if path.is_dir():
                yield from path.rglob(pattern)
