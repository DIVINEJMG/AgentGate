# ruff: noqa: I001

from pathlib import Path

from app.bootstrap.settings import settings
from app.execution.browser.http_reader import (
    MAX_HTTP_FORMS,
    MAX_HTTP_LINKS,
    MAX_HTTP_TEXT,
    _BoundedHTMLParser,
)
from app.execution.browser.policy import BrowserDomainPolicy
from app.execution.providers.browser import _browser_session_ttl


ROOT = Path(__file__).resolve().parents[2]


def test_f32_lean_browser_policy_is_the_default() -> None:
    policy = BrowserDomainPolicy.from_configuration(
        {"allowedOrigins": "https://example.test"}
    )

    assert policy.load_visual_resources is False
    assert policy.capture_dom_snapshot is False


def test_f32_http_first_parser_is_bounded() -> None:
    parser = _BoundedHTMLParser()
    html = (
        "<html><head><title>Products</title></head><body>"
        + ("word " * 5000)
        + "".join(
            f'<a href="/product/{index}">Product {index}</a>'
            for index in range(MAX_HTTP_LINKS + 50)
        )
        + "".join(
            '<form action="/inquire"><input name="email" required></form>'
            for _ in range(MAX_HTTP_FORMS + 20)
        )
        + "</body></html>"
    )
    parser.feed(html)

    assert len(parser.visible_text) <= MAX_HTTP_TEXT
    assert len(parser.links) == MAX_HTTP_LINKS
    assert len(parser.forms) == MAX_HTTP_FORMS


def test_f32_browser_memory_budget_defaults_target_512mb_service() -> None:
    assert settings.browser_idle_shutdown_seconds == 180
    assert settings.browser_session_ttl_seconds == 900
    assert settings.browser_max_active_sessions == 1
    assert settings.browser_max_sessions_per_organization == 1
    assert settings.browser_max_pages_per_session == 3
    assert settings.browser_memory_soft_limit_percent == 85
    assert settings.browser_memory_hard_limit_percent == 90
    assert settings.browser_launch_min_headroom_bytes == 256 * 1024 * 1024
    assert settings.browser_runtime_retry_limit == 4
    assert settings.browser_runtime_retry_backoff_seconds == 20
    assert settings.browser_http_read_max_bytes <= 1_000_000
    assert settings.browser_cold_start_timeout_seconds == 65
    assert settings.browser_action_timeout_seconds == 45


def test_managed_browser_uses_current_ttl_without_changing_manual_resource() -> None:
    assert _browser_session_ttl(
        {"managedBy": "worker_autonomy", "sessionTtlSeconds": "600"}
    ) == 900
    assert _browser_session_ttl({"sessionTtlSeconds": "600"}) == 600


def test_f32_chromium_is_lazy_and_runtime_sweep_reaps_sessions() -> None:
    lifecycle = (ROOT / "backend/app/bootstrap/lifecycle.py").read_text(encoding="utf-8")
    runtime_api = (ROOT / "backend/app/api/internal/runtime.py").read_text(encoding="utf-8")
    browser_runtime = (
        ROOT / "backend/app/execution/browser/runtime.py"
    ).read_text(encoding="utf-8")

    assert "browser_provider.warmup()" not in lifecycle
    assert "browser process will start on first browser action" in lifecycle
    assert "await browser_provider.reap_expired_sessions()" in runtime_api
    assert "await asyncio.sleep(self._idle_shutdown_seconds)" in browser_runtime
    assert '"browser-capacity"' in browser_runtime
    assert "after_chromium_shutdown" in browser_runtime


def test_f32_observation_and_evidence_paths_are_memory_bounded() -> None:
    observation = (
        ROOT / "backend/app/execution/browser/observation.py"
    ).read_text(encoding="utf-8")
    provider = (
        ROOT / "backend/app/execution/providers/browser.py"
    ).read_text(encoding="utf-8")

    assert "MAX_VISIBLE_TEXT = 12_000" in observation
    assert "MAX_ARIA_SNAPSHOT = 6_000" in observation
    assert "MAX_ELEMENTS = 120" in observation
    assert "MAX_FORMS = 20" in observation
    assert "include_dom_snapshot: bool = False" in observation
    assert "capture_after = (" in provider
    assert "request.capability.side_effect" in provider
    assert 'request.operation in {"form.submit", "auth.login", "file.upload", "file.download"}' in provider
    assert "optional_evidence_allowed" in provider


def test_f32_http_first_and_provider_neutral_capacity_backoff_are_durable() -> None:
    managed = (ROOT / "backend/app/runtime/managed.py").read_text(encoding="utf-8")
    authority = (
        ROOT / "backend/app/application/services/browser_origin_authority.py"
    ).read_text(encoding="utf-8")
    coordinator = (
        ROOT / "backend/app/infrastructure/redis/coordination.py"
    ).read_text(encoding="utf-8")

    assert "fetch_http_page(" in managed
    assert '"browser.http.bootstrap"' in managed
    assert "httpFirstObservations" in managed
    assert "providerRetryAt" in managed
    assert "runtime_provider_retry_limit" in managed
    assert "runtime_provider_retry_backoff_seconds" in managed
    assert "browser_runtime_retry_limit" in managed
    assert "browser_runtime_retry_backoff_seconds" in managed
    assert '"browser_capacity_unavailable"' in managed
    assert "providerRetryCount" in managed
    assert '"loadVisualResources": "false"' in authority
    assert '"captureDomSnapshot": "false"' in authority
    assert '"sessionTtlSeconds": str(settings.browser_session_ttl_seconds)' in authority
    assert '"maxPages": str(settings.browser_max_pages_per_session)' in authority
    assert "async def renew_lock" in coordinator



def test_f32_cold_browser_start_has_separate_budget_and_reclaim_path() -> None:
    provider = (
        ROOT / "backend/app/execution/providers/browser.py"
    ).read_text(encoding="utf-8")
    runtime = (
        ROOT / "backend/app/execution/browser/runtime.py"
    ).read_text(encoding="utf-8")
    settings_source = (
        ROOT / "backend/app/bootstrap/settings.py"
    ).read_text(encoding="utf-8")

    assert "browser_cold_start_timeout_seconds" in settings_source
    assert "browser_action_timeout_seconds" in settings_source
    assert 'request.operation == "navigation.open"' in provider
    assert "budget = settings.browser_action_timeout_seconds +" in provider
    assert "create_session reserves a capacity slot before launching Chromium" in provider
    assert "async def prepare(self) -> None:" in runtime
    assert "async def shutdown_if_idle(self) -> None:" in runtime
    assert "self._browser is None and self._playwright is None" in runtime
    assert "_launch_min_headroom_bytes" in runtime
    assert "Browser launch deferred" in runtime
    assert "BrowserCapacityUnavailable" in runtime
    assert "chromium_process_ids" in runtime
    assert "async def _reap_orphaned_chromium" in runtime
    assert "signal.SIGTERM" in runtime
    # Available force signals are exercised by test_browser_orphan_cleanup;
    # Windows has no SIGKILL, so its literal use is not a portability requirement.
    assert "baseline_chromium_pids" in runtime
    assert 'telemetry_logger = logging.getLogger("uvicorn.error")' in runtime


def test_f32_retryable_provider_errors_are_bounded_and_requeued() -> None:
    managed = (ROOT / "backend/app/runtime/managed.py").read_text(encoding="utf-8")
    settings_source = (
        ROOT / "backend/app/bootstrap/settings.py"
    ).read_text(encoding="utf-8")

    assert "runtime_provider_retry_limit: int = 2" in settings_source
    assert "runtime_provider_retry_backoff_seconds: int = 5" in settings_source
    assert "browser_runtime_retry_limit: int = 4" in settings_source
    assert "browser_runtime_retry_backoff_seconds: int = 20" in settings_source
    assert "exc.error.retryable" in managed
    assert "provider_retry_count < retry_limit" in managed
    assert "2**provider_retry_count" in managed
    assert 'item.status = "queued"' in managed
    assert "providerRetryCode=exc.error.code" in managed


def test_f32_http_first_bootstrap_is_visible_in_runtime_telemetry() -> None:
    managed = (ROOT / "backend/app/runtime/managed.py").read_text(encoding="utf-8")

    assert "HTTP-first bootstrap completed" in managed
    assert "HTTP-first bootstrap skipped" in managed
    assert "HTTP-first bootstrap cache reused" in managed
    assert 'telemetry_logger = logging.getLogger("uvicorn.error")' in managed
