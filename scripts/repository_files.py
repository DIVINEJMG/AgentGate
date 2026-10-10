"""Enumerate tracked and nonignored source candidates without walking dependencies."""
from pathlib import Path
import subprocess


def repository_files(root: Path) -> tuple[Path, ...]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, check=True, capture_output=True, timeout=30,
    )
    return tuple(
        root / name for name in sorted(set(result.stdout.decode("utf-8").split("\0")))
        if name and (root / name).is_file()
    )
