from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".md",
    ".env",
    ".example",
}
PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("NVIDIA API key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{32,}\b")),
    ("OpenAI-style API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b")),
    (
        "assigned AI provider key",
        re.compile(
            r"AI_PROVIDER_API_KEY\s*=\s*['\"]?([^<\s'\"]{24,})",
            re.IGNORECASE,
        ),
    ),
)
SAFE_MARKERS = (
    "example",
    "placeholder",
    "your_",
    "your-",
    "<nvidia",
    "<your",
    "<secret",
    "redacted",
    "test-secret",
    "fake",
)


def tracked_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
    )
    return [ROOT / line for line in output.splitlines() if line.strip()]


errors: list[str] = []
for path in tracked_files():
    if not path.is_file():
        continue
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".env.example"}:
        continue
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        continue
    for line_number, line in enumerate(text.splitlines(), start=1):
        lowered = line.lower()
        if any(marker in lowered for marker in SAFE_MARKERS):
            continue
        for label, pattern in PATTERNS:
            if pattern.search(line):
                errors.append(
                    f"{path.relative_to(ROOT)}:{line_number}: possible committed {label}"
                )

if errors:
    for error in errors:
        print(f"Secret hygiene failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("Secret hygiene scan passed: no committed model/provider credential patterns found.")
