from zoneinfo import ZoneInfoNotFoundError

import pytest

from app.application.services import schedule_inference


def test_valid_timezone_is_preserved_without_diagnostic(caplog):
    assert schedule_inference._validate_timezone("UTC") == "UTC"
    assert "schedule_timezone_lookup_failed" not in caplog.text


def test_hidden_character_is_visible_in_diagnostic(monkeypatch, caplog):
    def lookup(value):
        if value == "UTC":
            return object()
        raise ZoneInfoNotFoundError("No time zone found with key " + value)
    monkeypatch.setattr(schedule_inference, "ZoneInfo", lookup)
    with pytest.raises(ValueError, match="Unknown schedule timezone") as error:
        schedule_inference._validate_timezone("UTC\u200b")
    assert "\\u200b" in str(error.value)
    assert "U+200B" in caplog.text
    assert "utc_available=True" in caplog.text
    assert isinstance(error.value.__cause__, ZoneInfoNotFoundError)


def test_missing_database_is_not_reported_as_unknown_timezone(monkeypatch, caplog):
    def lookup(value):
        raise ZoneInfoNotFoundError("No time zone found with key " + value)
    monkeypatch.setattr(schedule_inference, "ZoneInfo", lookup)
    monkeypatch.setattr(schedule_inference, "find_spec", lambda name: None)
    with pytest.raises(ValueError, match="Backend timezone data is unavailable"):
        schedule_inference._validate_timezone("UTC")
    assert "tzdata_available=False" in caplog.text
    assert "utc_available=False" in caplog.text
    assert "ZoneInfoNotFoundError" in caplog.text
