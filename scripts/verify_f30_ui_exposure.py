from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"F30 Browser UI exposure acceptance failed: {message}")


def main() -> None:
    integration_api = text("frontend/src/lib/integrationApi.ts")
    capability_api = text("frontend/src/lib/capabilityApi.ts")
    integrations = text("frontend/src/components/IntegrationsPanel.tsx")
    browser_form = text("frontend/src/components/BrowserConnectionFields.tsx")
    worker_quick_start = text("frontend/src/components/WorkerQuickStart.tsx")
    jobs = text("frontend/src/components/JobsPanel.tsx")
    policies = text("frontend/src/components/PoliciesPanel.tsx")
    backend = text("backend/app/api/integration_capability_routes.py")
    vercel = text("vercel.json")

    require(
        "'browser'" in integration_api and "browser:'browser'" in integration_api,
        "frontend integration provider/route does not include Browser",
    )
    require(
        "provider: IntegrationProvider" in capability_api
        and "provider: 'github'" not in capability_api,
        "capability catalog is still typed as GitHub-only",
    )
    require(
        "provider:'browser'" in integrations
        and "Governed Browser" in integrations
        and "BrowserConnectionFields" in integrations,
        "Connections UI does not expose the Governed Browser adapter",
    )

    for field in (
        "startUrl",
        "allowedOrigins",
        "deniedOrigins",
        "allowedPaths",
        "deniedPaths",
        "enableFileTransfer",
        "allowPrivateNetwork",
    ):
        require(field in browser_form, f"Browser connection form missing {field}")
    require(
        "new URL(startUrl)" in browser_form or "new URL(raw)" in browser_form,
        "Browser connection form does not validate an absolute HTTP/HTTPS start URL",
    )
    require(
        "Leave blank to allow only the Start URL origin" in browser_form,
        "Browser form does not communicate fail-closed origin defaults",
    )
    require(
        "localhost" in browser_form and "cloud metadata" in browser_form,
        "Browser form does not explain private-network/metadata protection",
    )

    require(
        '"browser": "browser"' in backend,
        "backend integration route map does not explicitly expose Browser",
    )
    require(
        '"availableCapabilities": list(resource.available_capabilities)' in backend,
        "connection does not persist provider-discovered resource capabilities",
    )
    require(
        'config.get("availableCapabilities")' in backend,
        "capability catalog ignores resource-specific availability",
    )
    require(
        "capability.requires_credential and not credential_configured" in backend,
        "catalog exposes credential-required capabilities without a vault credential",
    )

    require(
        "catalog.scopes" in worker_quick_start
        and "requiredCapabilities" in worker_quick_start,
        "Worker Quick Start does not consume the dynamic capability catalog",
    )
    require(
        "listCapabilityCatalog" in jobs and "requiredCapabilities" in jobs,
        "Jobs UI does not consume the dynamic capability catalog",
    )
    require(
        "listCapabilityCatalog" in policies
        and "catalog?.resources" in policies
        and "catalog?.scopes" in policies,
        "Policies UI does not consume dynamic Browser resources/scopes",
    )

    require(
        '"deploymentEnabled": false' in vercel,
        "Vercel automatic deployments must remain disabled",
    )

    print("F30 governed Browser UI exposure acceptance passed.")


if __name__ == "__main__":
    main()
