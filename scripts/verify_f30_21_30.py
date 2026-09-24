from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"F30.21-F30.30 acceptance failed: {message}")


def main() -> None:
    runtime = text(APP / "execution" / "browser" / "runtime.py")
    provider = text(APP / "execution" / "providers" / "browser.py")
    observation = text(APP / "execution" / "browser" / "observation.py")
    sensitive = text(APP / "execution" / "browser" / "sensitive.py")
    redaction = text(APP / "execution" / "redaction.py")
    artifacts = text(APP / "execution" / "browser" / "artifacts.py")
    verification = text(APP / "execution" / "browser" / "verification.py")
    contracts = text(APP / "execution" / "browser" / "contracts.py")
    action_tx = text(APP / "infrastructure" / "database" / "action_transaction.py")
    executor = text(APP / "execution" / "provider_executor.py")
    lifecycle = text(APP / "execution" / "lifecycle.py")
    resolver = text(APP / "execution" / "providers" / "resolver.py")
    runtime_worker = text(APP / "runtime" / "worker.py")

    require(
        "is_sensitive_field_metadata" in sensitive
        and "one-time-code" in sensitive
        and "password" in redaction,
        "sensitive-field detection is incomplete",
    )
    require(
        "redacted_dict(payload)" in action_tx,
        "Action persistence does not redact sensitive request structures",
    )
    require(
        "mask=masks" in runtime and "page.screenshot" in runtime,
        "screenshot evidence is not captured with sensitive field masking",
    )
    require(
        "browser_before_action" in provider
        and "browser_after_action" in provider
        and "browser_verification_failure" in provider,
        "required screenshot checkpoints are missing",
    )
    require(
        "BrowserArtifactReference" in artifacts
        and "Artifact(" in artifacts
        and "checksum_sha256" in artifacts,
        "Browser evidence/downloads do not use canonical artifact storage",
    )
    require(
        '"actionEvidence"' in provider
        and '"sessionId"' in provider
        and '"correlationId"' in provider
        and '"stateChanged"' in provider,
        "normalized Browser action evidence is incomplete",
    )
    require(
        "record_execution_evidence" in executor
        and 'event_type="action.executed"' in executor
        and 'topic="action.executed"' in executor,
        "Browser/provider evidence is not persisted through provider-neutral Audit/Outbox",
    )

    require(
        "expect_download" in runtime and "max_bytes" in runtime and "browser_download" in provider,
        "governed download interception/storage boundary is missing",
    )
    require(
        "set_input_files" in runtime
        and '"buffer": content' in runtime
        and "load_input" in artifacts,
        "upload does not use authorized artifact bytes at the provider edge",
    )
    require(
        "not authorized for this Worker" in artifacts
        and "not authorized for this Run" in artifacts
        and "governed Worker/Run binding" in artifacts,
        "upload artifact references are not bound to the authorized Worker/Run",
    )
    require(
        "enableFileTransfer" in provider and "declared -= FILE_TRANSFER_SCOPES" in provider,
        "file-transfer permissions do not fail closed",
    )

    require(
        'context.on("page"' in runtime and "handle.pages" in runtime,
        "popup/new-tab tracking is missing",
    )
    require(
        "blocked_popup_initial_navigation" in runtime
        and "frame_unavailable" in runtime
        and "await self._raise_if_blocked(handle)" in runtime,
        "blocked popup policy decisions are not surfaced and cleaned up",
    )
    require(
        "frame_name" in contracts
        and "frame_origin" in contracts
        and "_frame_for_locator" in runtime
        and "policy.permits(selected.url" in runtime,
        "iframe interactions are not bound to domain policy",
    )
    require(
        "_handle_dialog" in runtime
        and "await dialog.dismiss()" in runtime
        and 'action == "accept"' in runtime,
        "dialog handling is not fail-closed",
    )
    require(
        "Accepting a confirmation/prompt dialog is restricted" in provider,
        "destructive dialog acceptance lacks capability restriction",
    )

    for method in (
        "urlChangedFrom",
        "elementAppeared",
        "elementDisappeared",
        "formState",
        "pageState",
        "downloadCreated",
        "successIndicator",
        "errorIndicator",
    ):
        require(method in verification, f"verification method missing: {method}")
    require(
        "verify_browser_state" in runtime
        and "stateChanged" in provider
        and "verification_failed" in provider,
        "Browser success is not tied to observable verification",
    )

    require(
        "ObserveActVerifyLifecycle" in executor
        and 'LifecycleCheckpoint("observe"' in lifecycle
        and 'LifecycleCheckpoint("act"' in lifecycle
        and 'LifecycleCheckpoint("verify"' in lifecycle,
        "Browser is not using the existing F29 Observe→Act→Verify lifecycle",
    )

    for forbidden in (
        'provider == "browser"',
        'provider in {"browser"',
        "if browser",
    ):
        require(
            forbidden not in executor,
            f"provider-specific core executor branch found: {forbidden}",
        )
        require(
            forbidden not in resolver,
            f"provider-specific resolver branch found: {forbidden}",
        )
        require(
            forbidden not in runtime_worker,
            f"provider-specific runtime worker branch found: {forbidden}",
        )

    require(
        '"deploymentEnabled": false' in text(ROOT / "vercel.json"),
        "Vercel automatic deployments must remain disabled",
    )

    # Observation output must remain browser-neutral/model-safe.
    require(
        "BrowserObservation" in observation and "raw Playwright" not in observation,
        "Browser observations no longer preserve the normalized boundary",
    )

    print("F30.21-F30.30 browser evidence/file/verification acceptance passed.")


if __name__ == "__main__":
    main()
