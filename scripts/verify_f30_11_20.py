from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"F30.11-F30.20 acceptance failed: {message}")


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> None:
    provider = text(APP / "execution" / "providers" / "browser.py")
    runtime = text(APP / "execution" / "browser" / "runtime.py")
    policy = text(APP / "execution" / "browser" / "policy.py")
    credentials = text(APP / "execution" / "browser" / "credentials.py")
    observation = text(APP / "execution" / "browser" / "observation.py")
    authorization = text(APP / "execution" / "authorization.py")
    gateway = text(APP / "domain" / "actions" / "gateway.py")

    for operation in ("form.fill", "form.submit", "auth.login", "file.upload", "file.download"):
        require(operation in provider, f"browser capability missing: {operation}")

    require("form_details" in observation, "structured form discovery missing")
    require(
        "Submit controls must use the governed browser.form.submit capability." in runtime,
        "generic click can bypass form-submit governance",
    )
    require(
        "Form submission by keyboard must use browser.form.submit." in runtime,
        "keyboard submit bypass is not blocked",
    )
    require("allowed_origins" in policy, "allowed-origin policy missing")
    require("denied_origins" in policy, "denied-origin policy missing")
    require("allowed_path_prefixes" in policy, "path restriction policy missing")
    require("BrowserNavigationBlocked" in runtime, "navigation policy enforcement missing")
    require("request.is_navigation_request()" in runtime, "redirect/navigation inspection missing")
    require(
        "route.fetch(max_redirects=0)" in runtime,
        "redirect chains are not inspected before browser follow-up",
    )

    require('risk="high"' in provider, "high-risk browser classification missing")
    require("effective_risk" in gateway, "capability risk is not fed into Action Gateway")
    require("action_fingerprint" in authorization, "approval action fingerprint missing")
    require(
        "approval_action_fingerprint" in authorization and "exact governed action" in gateway,
        "approval is not bound to the exact paused action",
    )

    for field in (
        "resource_external_id",
        "resource_origin",
        "policy_outcome",
        "risk",
        "approval_required",
        "approval_recommendation",
        "adapter_version",
        "credential_strategy",
    ):
        require(field in authorization, f"authorization provenance field missing: {field}")

    require("BrowserCredentialBundle" in credentials, "runtime credential bundle missing")
    require("credentialKey" in provider, "credential reference binding contract missing")
    require("credentials.resolve" in runtime, "runtime-only credential injection missing")
    require(
        "GET form submission" in runtime,
        "authentication GET credential-leak protection missing",
    )
    require("authentication_error" in provider, "normalized authentication error missing")

    resolver = text(APP / "execution" / "providers" / "resolver.py")
    executor = text(APP / "execution" / "provider_executor.py")
    for forbidden in ('provider == "browser"', 'provider in {"browser"', "if browser"):
        require(forbidden not in resolver, f"provider-specific resolver branch found: {forbidden}")
        require(forbidden not in executor, f"provider-specific executor branch found: {forbidden}")

    require(
        '"deploymentEnabled": false' in text(ROOT / "vercel.json"),
        "Vercel automatic deployments must remain disabled",
    )
    print("F30.11-F30.20 governed browser policy/auth acceptance passed.")


if __name__ == "__main__":
    main()
