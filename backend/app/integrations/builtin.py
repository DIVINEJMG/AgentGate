from app.domain.integrations.contracts import (
    IntegrationAdapter,
    IntegrationExecutionResult,
    IntegrationManifest,
)


class ManifestOnlyAdapter(IntegrationAdapter):
    def __init__(self, provider: str, manifests: tuple[IntegrationManifest, ...]) -> None:
        self.provider = provider
        self.manifests = manifests

    async def execute(
        self,
        *,
        operation: str,
        configuration: dict[str, str],
        credential: str | None,
        payload: dict[str, object],
    ) -> IntegrationExecutionResult:
        raise RuntimeError(
            f"{self.provider}:{operation} execution is not enabled yet; F26.15 exposes manifests only."
        )


def _manifest(provider: str, resource: str, action: str, scope: str, risk: str, *, side_effect: bool) -> IntegrationManifest:
    return IntegrationManifest(
        provider=provider,
        resource=resource,
        capability=f"{provider}.{action}",
        action=action,
        scope=scope,
        risk=risk,  # type: ignore[arg-type]
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effect=side_effect,
        approval_default=side_effect,
    )


github_adapter = ManifestOnlyAdapter(
    "github",
    (
        _manifest("github", "repository", "read", "github.repository.read", "low", side_effect=False),
        _manifest("github", "repository", "write", "github.repository.write", "high", side_effect=True),
    ),
)
gmail_adapter = ManifestOnlyAdapter(
    "gmail",
    (
        _manifest("gmail", "message", "read", "gmail.message.read", "medium", side_effect=False),
        _manifest("gmail", "message", "send", "gmail.message.send", "high", side_effect=True),
    ),
)
slack_adapter = ManifestOnlyAdapter(
    "slack",
    (
        _manifest("slack", "message", "read", "slack.message.read", "low", side_effect=False),
        _manifest("slack", "message", "send", "slack.message.send", "high", side_effect=True),
    ),
)
drive_adapter = ManifestOnlyAdapter(
    "google_drive",
    (
        _manifest("google_drive", "file", "read", "drive.file.read", "medium", side_effect=False),
        _manifest("google_drive", "file", "write", "drive.file.write", "high", side_effect=True),
    ),
)
calendar_adapter = ManifestOnlyAdapter(
    "google_calendar",
    (
        _manifest("google_calendar", "event", "read", "calendar.event.read", "low", side_effect=False),
        _manifest("google_calendar", "event", "write", "calendar.event.write", "high", side_effect=True),
    ),
)
