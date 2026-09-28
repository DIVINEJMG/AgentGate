# ruff: noqa: I001

from pathlib import Path

import httpx
import pytest

from app.infrastructure.qstash import provider as qstash_provider


ROOT = Path(__file__).resolve().parents[2]


def _response(status: int, text: str) -> httpx.Response:
    return httpx.Response(
        status,
        text=text,
        request=httpx.Request("POST", "https://qstash.example/v2/publish/test"),
    )


def test_qstash_daily_quota_is_classified_separately() -> None:
    with pytest.raises(qstash_provider.QStashRateLimitedError) as caught:
        qstash_provider.raise_for_qstash_status(_response(429, "daily ratelimit 1000 exceeded"))

    assert caught.value.daily_quota_exhausted is True


def test_qstash_transient_rate_limit_is_not_called_daily_quota() -> None:
    with pytest.raises(qstash_provider.QStashRateLimitedError) as caught:
        qstash_provider.raise_for_qstash_status(_response(429, "too many concurrent requests"))

    assert caught.value.daily_quota_exhausted is False


def test_work_queue_exposes_dispatch_health_without_mutating_work_items() -> None:
    trigger = (ROOT / "backend/app/runtime/qstash_trigger.py").read_text(
        encoding="utf-8"
    )
    routes = (ROOT / "backend/app/api/jobs_routes.py").read_text(encoding="utf-8")
    runtime_routes = (
        ROOT / "backend/app/api/runtime_result_routes.py"
    ).read_text(encoding="utf-8")
    jobs_api = (ROOT / "frontend/src/lib/jobsApi.ts").read_text(encoding="utf-8")
    jobs_panel = (ROOT / "frontend/src/components/JobsPanel.tsx").read_text(
        encoding="utf-8"
    )

    assert '"runtime-dispatch-health"' in trigger
    assert '"quota_exhausted"' in trigger
    assert "Work remains safely queued" in trigger
    assert "get_runtime_dispatch_health" in routes
    assert '"dispatch": await get_runtime_dispatch_health()' in routes
    assert "request_runtime_execution_detailed" in runtime_routes
    assert '"dispatch": execution_signal.as_dict()' in runtime_routes
    assert "RuntimeDispatchHealth" in jobs_api
    assert "Runtime dispatch delayed." in jobs_panel
    assert "dispatchBlocked" in jobs_panel


def test_qstash_recovery_cadence_preserves_free_plan_headroom() -> None:
    docs = (ROOT / "docs/f27-qstash.md").read_text(encoding="utf-8")

    assert "*/10 * * * *" in docs
    assert "*/15 * * * *" in docs
    assert "264 scheduled QStash messages per day" in docs
    assert "Do not restore the old every-minute runtime sweep" in docs
