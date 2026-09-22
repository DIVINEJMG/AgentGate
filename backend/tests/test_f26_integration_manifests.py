from app.integrations.builtin import (
    calendar_adapter,
    drive_adapter,
    github_adapter,
    gmail_adapter,
    slack_adapter,
)


def test_builtin_adapters_publish_required_manifest_fields() -> None:
    adapters = (github_adapter, gmail_adapter, slack_adapter, drive_adapter, calendar_adapter)
    for adapter in adapters:
        assert adapter.manifests
        for manifest in adapter.manifests:
            assert manifest.provider
            assert manifest.resource
            assert manifest.capability
            assert manifest.action
            assert manifest.scope
            assert manifest.risk in {"low", "medium", "high", "critical"}
            assert isinstance(manifest.side_effect, bool)
            assert isinstance(manifest.approval_default, bool)


def test_side_effects_default_to_approval() -> None:
    for manifest in github_adapter.manifests:
        if manifest.side_effect:
            assert manifest.approval_default is True
