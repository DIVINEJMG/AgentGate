import asyncio
from types import SimpleNamespace

import pytest

from app.domain.ai.providers import AIProviderError, ModelProviderCapabilities
from scripts import benchmark_ai_common as bench


def test_benchmark_summary_does_not_treat_timeouts_as_successful_latency():
    rows = [
        {"provider": "p", "model": "m", "status": s, "responseSeconds": t}
        for s, t in [
            ("valid", 2),
            ("valid", 4),
            ("timeout", None),
            ("invalid_provider_response", None),
        ]
    ]
    result = bench.summarize(rows)[0]
    assert result["medianSeconds"] == 3
    assert result["valid"] == 2
    assert result["timeouts"] == 1


def test_complex_checks_parse_python_without_executing_it():
    changes = [
        {"path": p, "content": "raise RuntimeError('must never execute')"} for p in bench.PATHS
    ]
    assert bench.quality_checks(
        "complex",
        {
            "changes": changes,
            "testsActuallyRun": False,
            "publication": {"draft": True, "merge": False},
        },
    )["pythonSyntaxValid"]
    changes[0]["content"] = "invalid python !!!"
    assert not bench.quality_checks("complex", {"changes": changes})["pythonSyntaxValid"]


@pytest.mark.asyncio
async def test_benchmark_timeout_records_wait_and_no_fake_response_time(monkeypatch):
    class Provider:
        name = "fake"
        capabilities = ModelProviderCapabilities(text_input=True, structured_json=True)

        def validate_request(self, **kwargs):
            pass

        async def generate_text(self, **kwargs):
            await asyncio.sleep(1)

    monkeypatch.setattr(
        bench,
        "transport_for_text_route",
        lambda *args, **kw: (Provider(), "secret", SimpleNamespace(model_dump=dict)),
    )
    row = await bench.run_trial({"provider": "fake", "model": "m"}, "interactive", 1, 0.02)
    assert row["status"] == "timeout"
    assert row["responseSeconds"] is None
    assert row["elapsedSeconds"] >= 0.02
    assert len(row["calls"]) == 1


@pytest.mark.asyncio
async def test_benchmark_validates_output_and_hides_provider_error_body(monkeypatch):
    class Provider:
        name = "fake"
        capabilities = ModelProviderCapabilities(text_input=True, structured_json=True)

        def validate_request(self, **kwargs):
            pass

        async def generate_text(self, **kwargs):
            raise AIProviderError(
                "authentication_failed",
                "SECRET_PROVIDER_BODY",
                retryable=False,
                account_scoped=True,
            )

    monkeypatch.setattr(
        bench,
        "transport_for_text_route",
        lambda *args, **kw: (Provider(), "secret", SimpleNamespace(model_dump=dict)),
    )
    row = await bench.run_trial({"provider": "fake", "model": "m"}, "interactive", 1, 1)
    assert row["status"] == "authentication_failed"
    assert "SECRET_PROVIDER_BODY" not in str(row)
