from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ACTIVE_TARGETS = (
    ROOT / "frontend",
    ROOT / "backend",
    ROOT / "infrastructure",
    ROOT / ".github",
    ROOT / "README.md",
    ROOT / "SECURITY.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "vercel.json",
)

FORBIDDEN = (
    "@app" + "deploy",
    "APP" + "DEPLOY",
    "app" + "deploy",
)

TEXT_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".yml", ".yaml",
    ".md", ".toml", ".ini", ".txt", ".css", ".html",
}


def iter_files(target: Path):
    if target.is_file():
        yield target
        return
    if not target.exists():
        return
    for path in target.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            yield path


def main() -> None:
    violations: list[str] = []
    for target in ACTIVE_TARGETS:
        for path in iter_files(target):
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if any(token.lower() in text for token in FORBIDDEN):
                violations.append(str(path.relative_to(ROOT)))

    if violations:
        joined = "\n".join(f"- {item}" for item in sorted(set(violations)))
        raise SystemExit(f"Active production references must be zero:\n{joined}")

    print("Active production references to the retired platform: 0")


if __name__ == "__main__":
    main()
