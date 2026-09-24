from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"
TESTS = ROOT / "backend" / "tests"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"F30.31-F30.40 acceptance failed: {message}")


def main() -> None:
    contracts = text(APP / "execution" / "contracts.py")
    provider = text(APP / "execution" / "providers" / "browser.py")
    runtime = text(APP / "execution" / "browser" / "runtime.py")
    browser_errors = text(APP / "execution" / "browser" / "errors.py")
    egress = text(APP / "execution" / "browser" / "egress.py")
    retry = text(APP / "execution" / "retry.py")
    recovery = text(APP / "execution" / "recovery.py")
    lifecycle = text(APP / "execution" / "lifecycle.py")
    executor = text(APP / "execution" / "provider_executor.py")
    resolver = text(APP / "execution" / "providers" / "resolver.py")
    bootstrap = text(APP / "execution" / "bootstrap.py")
    realtime = text(APP / "realtime" / "contracts.py")
    outbox = text(APP / "runtime" / "outbox_publisher.py")
    action_gateway = text(APP / "domain" / "actions" / "gateway.py")
    action_tx = text(APP / "infrastructure" / "database" / "action_transaction.py")
    observation = text(APP / "execution" / "browser" / "observation.py")
    redaction = text(APP / "execution" / "redaction.py")
    f30_0_10 = text(TESTS / "test_f30_0_10.py")
    f30_11_20 = text(TESTS / "test_f30_11_20.py")
    f30_21_30 = text(TESTS / "test_f30_21_30.py")
    f30_31_40 = text(TESTS / "test_f30_31_40.py")

    # F30.31 normalized Browser errors.
    for code in (
        "navigation_timeout",
        "element_not_found",
        "stale_observation",
        "detached_frame",
        "download_failure",
        "browser_crash",
        "runtime_limit_exceeded",
        "verification_failed",
    ):
        require(code in contracts, f"normalized execution error missing: {code}")
    for error_class in (
        "BrowserStaleObservation",
        "BrowserDetachedFrame",
        "BrowserElementNotFound",
        "BrowserDownloadFailure",
        "BrowserRuntimeLimitExceeded",
    ):
        require(error_class in browser_errors, f"Browser error class missing: {error_class}")
        require(error_class in provider, f"Browser provider does not normalize {error_class}")

    # F30.32 safe retry semantics.
    require(
        '"navigation_timeout"' in retry and '"browser_crash"' in retry,
        "safe Browser transient codes are not recognized by retry policy",
    )
    require(
        'error.code == "browser_crash" and request.capability.side_effect' in recovery,
        "side-effect crash replay is not explicitly blocked",
    )
    require(
        "retryable=not request.capability.side_effect" in provider,
        "Browser side-effect retryability is not fail-closed",
    )

    # F30.33 stale-page recovery.
    require(
        '"reobserve_replan"' in recovery,
        "stale state does not produce re-observe/replan recovery",
    )
    require(
        "recover_execution" in provider
        and "discard stale locators" in provider
        and "replan" in provider,
        "Browser stale recovery does not explicitly re-observe/replan",
    )
    require(
        "RecoverableExecutionProvider" in executor and "recover=recover" in executor,
        "generic lifecycle recovery hook is not wired",
    )
    require(
        "reobserve_replan" in lifecycle,
        "Observe/Act/Verify lifecycle cannot execute state-refresh recovery",
    )

    # F30.34 crash recovery.
    require(
        "_playwright_crash" in provider
        and 'code="browser_crash"' in provider
        and "_fail_quietly" in provider,
        "Browser crashes are not normalized and marked failed",
    )
    require(
        'status="failed"' in runtime,
        "Browser runtime cannot mark crashed sessions unhealthy",
    )

    # F30.35 runtime limits.
    for limit in (
        "MAX_SESSION_TTL_SECONDS",
        "MAX_NAVIGATION_TIMEOUT_MS",
        "MAX_ACTION_TIMEOUT_MS",
        "MAX_DOWNLOAD_BYTES",
    ):
        require(limit in provider, f"Browser provider limit missing: {limit}")
    require(
        "max_pages=8" in runtime and "Browser page limit exceeded" in runtime,
        "Browser page-count cap is missing",
    )
    for observation_cap in (
        "MAX_VISIBLE_TEXT",
        "MAX_DOM_SNAPSHOT",
        "MAX_ARIA_SNAPSHOT",
        "MAX_ELEMENTS",
    ):
        require(observation_cap in observation, f"observation cap missing: {observation_cap}")

    # F30.36 SSRF/private-network protection.
    for marker in (
        "localhost",
        "169.254.169.254",
        "is_private",
        "is_loopback",
        "is_link_local",
        "socket.getaddrinfo",
    ):
        require(marker in egress, f"Browser egress control missing: {marker}")
    require(
        "evaluate_browser_egress" in runtime,
        "Browser runtime does not apply egress preflight to navigation",
    )

    # F30.37 realtime through existing Outbox path.
    for event_type in (
        "browser.session.created",
        "browser.navigation.started",
        "browser.navigation.completed",
        "browser.observation.created",
        "browser.action.started",
        "browser.action.completed",
        "browser.verification.failed",
        "browser.session.closed",
    ):
        require(event_type in realtime, f"Browser realtime event type missing: {event_type}")
    require(
        '"realtimeEvents"' in provider,
        "Browser provider does not produce normalized realtime event intents",
    )
    require(
        "provider_events" in executor
        and "TransactionalOutbox" in executor
        and "REALTIME_EVENT_TYPES" in executor,
        "Browser realtime events do not enter transactional Outbox",
    )
    require(
        "RedisRealtimeBus" in outbox,
        "Outbox publisher does not feed the established Redis realtime bus",
    )

    # F30.38 provider conformance.
    require(
        "browser_provider" in bootstrap and "ProviderRegistry" in bootstrap,
        "Browser provider is not registered in the universal registry",
    )
    for surface in (
        "discover_resources",
        "discover_capabilities",
        "check_health",
        "discover_permissions",
        "normalize_input",
        "execute",
        "verify",
    ):
        require(
            f"async def {surface}" in provider,
            f"Browser provider conformance surface missing: {surface}",
        )
    require(
        "test_f30_38_browser_provider_conforms_to_universal_provider_surface"
        in f30_31_40,
        "Browser provider conformance test is missing",
    )

    # F30.39 tenant/security suite and fail-closed posture.
    for invariant in (
        "cookie",
        "localStorage",
        "Cross-organization",
        "allowedOrigins",
        "credential",
        "artifact",
        "approval",
    ):
        combined = f30_0_10 + f30_11_20 + f30_21_30 + f30_31_40
        require(
            invariant.lower() in combined.lower(),
            f"Browser security coverage missing invariant: {invariant}",
        )
    require(
        "evaluate_browser_egress" in f30_31_40
        and "/etc/passwd" in f30_31_40,
        "private-network and unauthorized-host-file tests are missing",
    )

    # F30.40 whole-path acceptance.
    require(
        "CapabilityResolver" not in resolver or "provider == \"browser\"" not in resolver,
        "core resolver contains a Browser-specific execution branch",
    )
    require(
        'provider == "browser"' not in executor
        and 'provider in {"browser"' not in executor,
        "core provider executor contains Browser-specific branches",
    )
    require(
        "class ActionGateway" in action_gateway
        and "Action Gateway authorization already satisfied." in lifecycle
        and "snapshot" in executor
        and "Authorization snapshot" in executor,
        "Browser execution is not downstream of Action Gateway authorization",
    )
    require(
        "action.executed" in executor
        and "actionEvidence" in executor
        and "artifactIds" in executor,
        "Audit/evidence/artifact provenance is incomplete",
    )
    require(
        "redacted_dict(payload)" in action_tx
        and "redact_sensitive_structure" in provider
        and "password" in redaction,
        "credentials/sensitive Browser data can reach persistent output",
    )
    require(
        "verification.verified" in executor and "browser.verification.failed" in realtime,
        "verification is not part of completion/realtime",
    )
    require(
        "Direct provider execution is disabled; execute through ActionGateway." in executor,
        "Browser can bypass Action Gateway via direct universal executor execution",
    )

    require(
        '"deploymentEnabled": false' in text(ROOT / "vercel.json"),
        "Vercel automatic deployments must remain disabled",
    )

    print("F30.31-F30.40 final Governed Browser Execution acceptance passed.")


if __name__ == "__main__":
    main()
