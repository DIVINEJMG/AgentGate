from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"F29.40 acceptance failed: {message}")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> None:
    runtime_paths = (
        APP / "runtime",
        APP / "domain" / "actions",
        APP / "execution" / "provider_executor.py",
    )
    provider_literals = (
        '"github"',
        '"gmail"',
        '"slack"',
        '"google_drive"',
        '"google_calendar"',
    )
    offenders: list[str] = []
    for path in runtime_paths:
        files = path.rglob("*.py") if path.is_dir() else (path,)
        for file in files:
            source = text(file)
            if any(
                token in source and ("provider ==" in source or "provider in" in source)
                for token in provider_literals
            ):
                offenders.append(str(file.relative_to(APP)))
    require(not offenders, f"provider-specific Runtime branches found: {offenders}")

    bootstrap = text(APP / "execution" / "bootstrap.py")
    for provider in ("github_provider", "gmail_provider", "slack_provider", "google_drive_provider", "google_calendar_provider"):
        require(provider in bootstrap, f"{provider} missing from universal registry")

    gateway = text(APP / "domain" / "actions" / "gateway.py")
    require("execute_request" in gateway, "Action Gateway universal request path missing")
    require("Provider permission snapshot" in gateway, "resource/provider authorization checks missing")

    realtime = text(APP / "realtime" / "bus.py")
    require("xadd" in realtime, "Redis Streams are not used")
    require("dedupe_key" in realtime, "realtime duplicate protection missing")

    realtime_routes = text(APP / "api" / "realtime_routes.py")
    require("/ws/v1/organizations/{organization_id}" in realtime_routes, "WebSocket route missing")
    require("/events/stream" in realtime_routes, "SSE route missing")
    require("/events" in realtime_routes, "replay endpoint missing")
    require("Cross-organization realtime subscription denied" in realtime_routes, "cross-org realtime isolation missing")
    require("_ensure_organization_access" in realtime_routes, "testable realtime tenant guard missing")

    frontend_realtime = text(ROOT / "frontend" / "src" / "platform" / "realtimeClient.ts")
    require("audoryn.realtime.cursor." in frontend_realtime, "frontend replay cursor persistence missing")
    require("replayThenConnect" in frontend_realtime, "frontend reconnect replay missing")

    outbox = text(APP / "runtime" / "outbox_publisher.py")
    require("drain_outbox_batch" in outbox, "outbox drain publisher missing")
    require("event_id=str(event.id)" in outbox, "stable outbox realtime event identity missing")

    outbox_route = text(APP / "api" / "internal" / "outbox.py")
    require("/drain" in outbox_route, "QStash outbox drain endpoint missing")
    require("QStashSignatureVerifier" in outbox_route, "outbox drain signature verification missing")

    session = text(APP / "infrastructure" / "database" / "session.py")
    require("after_commit" in session, "post-commit outbox trigger missing")
    require("outbox_pending" in text(APP / "infrastructure" / "database" / "outbox.py"), "outbox commit marker missing")

    render = text(ROOT / "infrastructure" / "render" / "render.yaml")
    require("name: audoryn-outbox" not in render, "paid Render outbox worker must remain removed")
    require("QSTASH_OUTBOX_DRAIN_URL" in render, "QStash outbox drain URL config missing")

    metrics = text(APP / "observability" / "metrics.py")
    for metric in (
        "PROVIDER_EXECUTION_LATENCY",
        "CAPABILITY_SUCCESS",
        "VERIFICATION_FAILURE",
        "EXECUTION_RETRY",
        "EXECUTION_FALLBACK",
        "EVENT_DELIVERY_LATENCY",
        "PROVIDER_HEALTH",
    ):
        require(metric in metrics, f"observability metric missing: {metric}")

    provenance = text(APP / "execution" / "provenance.py")
    require("adapter_version" in provenance, "canonical adapter-version provenance missing")
    action_tx = text(APP / "infrastructure" / "database" / "action_transaction.py")
    require("audit_provenance_payload" in action_tx, "Audit execution provenance persistence missing")
    results = text(APP / "api" / "runtime_result_routes.py")
    require("executionProvenance" in results, "Results execution provenance missing")
    require("_run_execution_provenance" in results, "Result provenance aggregation missing")

    require((APP / "environment" / "model.py").exists(), "Environment Model foundation missing")
    require((APP / "execution" / "capability_resolver.py").exists(), "Capability Resolver missing")
    require((APP / "runtime" / "planner" / "contracts.py").exists(), "multi-provider planner contract missing")
    require((APP / "runtime" / "planner" / "delegation.py").exists(), "Worker delegation contract missing")
    require((APP / "execution" / "providers" / "browser.py").exists(), "Browser provider interface missing")
    require((APP / "execution" / "providers" / "extensible.py").exists(), "MCP/custom provider interface missing")

    vercel = text(ROOT / "vercel.json")
    require('"deploymentEnabled": false' in vercel, "Vercel automatic deployments must remain disabled")

    print("F29.31-F29.40 final cutover acceptance passed.")


if __name__ == "__main__":
    main()
