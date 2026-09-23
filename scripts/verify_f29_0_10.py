from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"

REQUIRED = (
    APP / "execution" / "contracts.py",
    APP / "execution" / "providers" / "base.py",
    APP / "execution" / "providers" / "registry.py",
    APP / "execution" / "providers" / "resolver.py",
    APP / "execution" / "provider_executor.py",
    APP / "execution" / "providers" / "native" / "github.py",
    APP / "execution" / "providers" / "native" / "gmail.py",
    APP / "execution" / "providers" / "native" / "slack.py",
    APP / "execution" / "providers" / "native" / "google_drive.py",
    APP / "execution" / "providers" / "native" / "google_calendar.py",
    ROOT / "docs" / "f29-universal-execution.md",
)

missing = [str(path.relative_to(ROOT)) for path in REQUIRED if not path.exists()]
if missing:
    raise SystemExit(f"F29.0-10 missing required surfaces: {missing}")

contracts = (APP / "execution" / "contracts.py").read_text(encoding="utf-8")
for symbol in (
    "class CapabilityDescriptor",
    "class ResourceDescriptor",
    "class ExecutionRequest",
    "class ExecutionResult",
    "class ExecutionError",
):
    if symbol not in contracts:
        raise SystemExit(f"F29 contract missing {symbol}")

provider_base = (
    APP / "execution" / "providers" / "base.py"
).read_text(encoding="utf-8")
for method in (
    "discover_resources",
    "discover_capabilities",
    "check_health",
    "execute",
    "verify",
):
    if f"def {method}" not in provider_base:
        raise SystemExit(f"F29 provider protocol missing {method}")

integration_routes = (
    APP / "api" / "integration_capability_routes.py"
).read_text(encoding="utf-8")
if "PROVIDERS:" in integration_routes:
    raise SystemExit("F29 capability API still contains a hard-coded provider catalog.")
if "execution_provider_registry" not in integration_routes:
    raise SystemExit("F29 capability API does not consume the provider registry.")

for relative in (
    "runtime",
    "domain/actions",
):
    for file in (APP / relative).rglob("*.py"):
        text = file.read_text(encoding="utf-8")
        if "provider == " in text or "provider in " in text:
            raise SystemExit(
                f"Provider-specific execution branch detected: {file.relative_to(ROOT)}"
            )

print("F29.0-F29.10 universal execution boundaries verified.")
