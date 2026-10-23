import pytest

from audoryn_shared.bootstrap.settings import settings


@pytest.fixture(autouse=True)
def _no_usage_records(monkeypatch: pytest.MonkeyPatch) -> None:
    """Usage records open their own database session; tests must never reach the configured
    database through them. Tests that check saving enable it against their own engine."""
    monkeypatch.setattr(settings, "usage_records_enabled", False)
