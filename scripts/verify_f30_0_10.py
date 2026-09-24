from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"F30.0-F30.10 acceptance failed: {message}")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> None:
    browser_provider = text(APP / "execution" / "providers" / "browser.py")
    bootstrap = text(APP / "execution" / "bootstrap.py")
    resolver = text(APP / "execution" / "providers" / "resolver.py")
    executor = text(APP / "execution" / "provider_executor.py")
    runtime = text(APP / "execution" / "browser" / "runtime.py")
    observation = text(APP / "execution" / "browser" / "observation.py")
    contracts = text(APP / "execution" / "browser" / "contracts.py")

    require('provider="browser"' in browser_provider, "browser provider manifest missing")
    require('kind="browser"' in browser_provider, "browser provider kind missing")
    require('version="1.0.0"' in browser_provider, "versioned browser adapter missing")
    require("browser_provider" in bootstrap, "browser is not in universal provider registry")

    for forbidden in ('provider == "browser"', 'provider in {"browser"', "if browser"):
        require(forbidden not in resolver, f"provider-specific resolver branch found: {forbidden}")
        require(forbidden not in executor, f"provider-specific executor branch found: {forbidden}")

    require("BrowserSession" in contracts, "normalized BrowserSession missing")
    for field in (
        "organization_id",
        "worker_id",
        "run_id",
        "browser_context_id",
        "expires_at",
        "current_url",
        "current_origin",
    ):
        require(field in contracts, f"BrowserSession field missing: {field}")

    require("new_context" in runtime, "isolated Chromium BrowserContext creation missing")
    require("Cross-organization browser session access denied" in runtime, "tenant guard missing")
    require("Cross-worker browser session access denied" in runtime, "worker guard missing")
    require("asyncio.CancelledError" in browser_provider, "browser cancellation cleanup missing")
    require("ManagedExecutionProvider" in text(APP / "bootstrap" / "lifecycle.py"), "managed provider shutdown hook missing")
    for lifecycle in ("create_session", "resume", "close", "expire", "terminate"):
        require(f"def {lifecycle}" in runtime, f"browser lifecycle method missing: {lifecycle}")

    require("BrowserObservation" in contracts, "normalized browser observation missing")
    require("aria_snapshot" in observation, "accessibility-tree observation missing")
    require("[REDACTED]" in observation, "sensitive observation redaction missing")
    require("script,style,noscript,template" in observation, "compact DOM cleanup missing")

    for strategy in ("observation_ref", "role", "label", "text", "css"):
        require(strategy in contracts, f"locator strategy missing: {strategy}")

    operations = (
        "navigation.open",
        "navigation.back",
        "navigation.forward",
        "navigation.reload",
        "navigation.follow_link",
        "element.click",
        "element.type",
        "element.clear",
        "element.select",
        "element.check",
        "element.uncheck",
        "element.press_key",
        "page.scroll",
        "element.hover",
    )
    for operation in operations:
        require(operation in browser_provider, f"browser primitive missing: {operation}")

    require(
        "Direct provider execution is disabled; execute through ActionGateway." in executor,
        "Action Gateway browser governance boundary missing",
    )
    vercel = text(ROOT / "vercel.json")
    require(
        '"deploymentEnabled": false' in vercel,
        "Vercel automatic deployments must remain disabled",
    )
    print("F30.0-F30.10 governed browser foundation acceptance passed.")


if __name__ == "__main__":
    main()
