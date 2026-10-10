"""Boundary scans include new source and tracked files even when ignored later."""
import importlib.util
import subprocess
from pathlib import Path


def test_repository_scan_preserves_tracked_and_new_files_without_generated_output(tmp_path):
    path = Path(__file__).resolve().parents[2] / "scripts/repository_files.py"
    spec = importlib.util.spec_from_file_location("repository_verification_files", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / "tracked.py").write_text("# tracked source\n")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("tracked.py\nnode_modules/\nartifacts/\n")
    (tmp_path / "new.py").write_text("# new source\n")
    for directory in ("node_modules", "artifacts"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "generated.json").write_text("{}")
    found = {file.relative_to(tmp_path).as_posix() for file in module.repository_files(tmp_path)}
    assert found == {".gitignore", "tracked.py", "new.py"}
