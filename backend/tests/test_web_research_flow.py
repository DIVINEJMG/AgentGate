from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import httpx
import pytest

from app.application.services import web_research, worker_site_targets
from app.domain.ai.providers import AIGateway, AIProviderError
from app.domain.conversation.intent import WorkerCommandIntent
from app.domain.workforce.drafts import SiteTargetDraft
from app.execution.bootstrap import execution_provider_registry
from app.execution.contracts import ExecutionRequest
from app.runtime.planner.adaptive import _last_browser_action_made_no_progress


class _Budget:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls = 0

    async def reserve_limit(self, *_args, **_kwargs) -> bool:
        self.calls += 1
        return self.allowed

    async def close(self) -> None:
        pass


class _Gateway:
    async def generate_text(self, **_kwargs):
        return SimpleNamespace(text="The inspected source supports the answer.")


def _source(provider: str = "tavily") -> web_research.InspectedSource:
    return web_research.InspectedSource(
        title="Official result", url="https://example.org/story",
        text="A verified public page with enough useful text to answer the question.",
        provider=provider, published_at="2026-10-01", observed_at="2026-10-03",
    )


def test_intent_schema_accepts_web_research() -> None:
    intent = WorkerCommandIntent(family="web.research", arguments={"query": "latest news"})
    assert intent.family == "web.research"


def test_search_falls_back_after_primary_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = _Budget()
    calls: list[str] = []
    monkeypatch.setattr(web_research.RedisCoordinator, "from_settings", lambda: budget)
    monkeypatch.setattr(web_research, "configured_sources", lambda: ("brave", "tavily"))

    async def brave(*_args):
        calls.append("brave")
        raise httpx.HTTPStatusError(
            "rate limited", request=httpx.Request("GET", "https://api.search.brave.com"),
            response=httpx.Response(429),
        )

    async def tavily(*_args):
        calls.append("tavily")
        return [web_research.SearchHit("tavily", "Official result", "https://example.org/story")]

    async def inspect(_hit):
        return _source()

    monkeypatch.setattr(web_research, "_search_brave", brave)
    monkeypatch.setattr(web_research, "_search_tavily", tavily)
    monkeypatch.setattr(web_research, "inspect_result", inspect)
    outcome = asyncio.run(web_research.research_web(
        query="latest result", required_source=None, organization_id=uuid4(),
        worker_id=uuid4(), thread_id=uuid4(), gateway=cast(AIGateway, _Gateway()),
    ))
    assert calls == ["brave", "tavily"]
    assert "https://example.org/story" in outcome.message
    assert "2026-10-01" in outcome.message
    assert outcome.sources[0].provider == "tavily"
    assert budget.calls == 4


def test_required_source_does_not_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = _Budget()
    calls: list[str] = []
    monkeypatch.setattr(web_research.RedisCoordinator, "from_settings", lambda: budget)
    monkeypatch.setattr(web_research, "configured_sources", lambda: ("brave", "tavily"))

    async def brave(*_args):
        calls.append("brave")
        return []

    async def tavily(*_args):
        calls.append("tavily")
        return []

    monkeypatch.setattr(web_research, "_search_brave", brave)
    monkeypatch.setattr(web_research, "_search_tavily", tavily)
    outcome = asyncio.run(web_research.research_web(
        query="latest result", required_source="brave", organization_id=uuid4(),
        worker_id=None, thread_id=uuid4(), gateway=cast(AIGateway, _Gateway()),
    ))
    assert calls == ["brave"]
    assert not outcome.sources
    assert "could not verify" in outcome.message.lower()


def test_required_website_never_substitutes_another_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budget = _Budget()
    monkeypatch.setattr(web_research.RedisCoordinator, "from_settings", lambda: budget)
    monkeypatch.setattr(web_research, "configured_sources", lambda: ("brave", "tavily"))

    async def unrelated(*_args):
        return [web_research.SearchHit("brave", "Elsewhere", "https://example.org/story")]

    async def forbidden(_hit):
        raise AssertionError("unrelated source must not be inspected")

    monkeypatch.setattr(web_research, "_search_brave", unrelated)
    monkeypatch.setattr(web_research, "_search_tavily", unrelated)
    monkeypatch.setattr(web_research, "inspect_result", forbidden)
    outcome = asyncio.run(web_research.research_web(
        query="latest ESPN result", required_source=None, required_site="ESPN",
        organization_id=uuid4(), worker_id=None, thread_id=uuid4(), gateway=cast(AIGateway, _Gateway()),
    ))
    assert "required site (ESPN)" in outcome.message
    assert not outcome.sources


def test_organization_budget_blocks_provider_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    budget = _Budget(allowed=False)
    monkeypatch.setattr(web_research.RedisCoordinator, "from_settings", lambda: budget)
    monkeypatch.setattr(web_research, "configured_sources", lambda: ("brave",))

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("provider must not be called")

    monkeypatch.setattr(web_research, "_search_brave", forbidden)
    outcome = asyncio.run(web_research.research_web(
        query="latest result", required_source=None, organization_id=uuid4(),
        worker_id=None, thread_id=uuid4(), gateway=cast(AIGateway, _Gateway()),
    ))
    assert "usage limit" in outcome.message


def test_private_result_url_is_never_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("private URL must not be fetched")

    monkeypatch.setattr(web_research, "fetch_http_page", forbidden)
    assert asyncio.run(web_research.inspect_result(
        web_research.SearchHit("brave", "internal", "http://127.0.0.1/admin")
    )) is None


def test_multiple_explicit_sites_resolve_without_search(monkeypatch: pytest.MonkeyPatch) -> None:
    async def forbidden(*_args):
        raise AssertionError("explicit sites need no search lookup")

    monkeypatch.setattr(worker_site_targets, "search_site_candidates", forbidden)
    origins = asyncio.run(worker_site_targets.resolve_site_targets(
        instruction="Check https://example.org and https://news.example.com each morning.",
        targets=[
            SiteTargetDraft(mention="https://example.org"),
            SiteTargetDraft(mention="https://news.example.com"),
        ],
    ))
    assert origins == ("https://example.org", "https://news.example.com")


def test_site_extraction_requires_human_authority_for_public_web() -> None:
    calls = []
    class FakeGateway:
        async def generate_structured(self, **_kwargs):
            calls.append(_kwargs)
            return {
                "targets": [], "public_web_requested": True,
                "public_web_request_excerpt": "search the public web",
                "job_providers": {"0": ["browser"]},
            }

    with pytest.raises(AIProviderError) as caught:
        asyncio.run(worker_site_targets.extract_site_assignments(
            gateway=cast(AIGateway, FakeGateway()), instruction="Check the assigned website only.",
            job_names=["Site check"], organization_id=uuid4(), thread_id=uuid4(),
        ))
    assert caught.value.category == "invalid_provider_response"
    assert isinstance(caught.value.__cause__, worker_site_targets.AmbiguousSiteTarget)
    assert "public web research" in str(caught.value.__cause__)
    assert len(calls) == 2  # Existing bounded correction must not grant authority.


def test_named_site_needs_unique_verified_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    async def candidates(_name, **_kwargs):
        return [
            web_research.SearchHit("brave", "Bestworth official", "https://bestworth.ng/"),
            web_research.SearchHit("brave", "Bestworth official", "https://bestworth.com/"),
        ]

    async def public(_url):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(worker_site_targets, "search_site_candidates", candidates)
    monkeypatch.setattr(worker_site_targets, "evaluate_browser_egress", public)
    with pytest.raises(worker_site_targets.AmbiguousSiteTarget):
        asyncio.run(worker_site_targets.resolve_site_targets(
            instruction="Check Bestworth every morning.",
            targets=[SiteTargetDraft(mention="Bestworth")],
        ))


def test_named_site_uses_independent_provider_agreement(monkeypatch: pytest.MonkeyPatch) -> None:
    async def candidates(_name, **_kwargs):
        return [
            web_research.SearchHit("brave", "Bestworth official", "https://bestworth.ng/"),
            web_research.SearchHit("tavily", "Bestworth official", "https://bestworth.ng/team"),
        ]

    async def public(_url):
        return SimpleNamespace(allowed=True)

    monkeypatch.setattr(worker_site_targets, "search_site_candidates", candidates)
    monkeypatch.setattr(worker_site_targets, "configured_sources", lambda: ("brave", "tavily"))
    monkeypatch.setattr(worker_site_targets, "evaluate_browser_egress", public)
    result = asyncio.run(worker_site_targets.resolve_site_targets(
        instruction="Check Bestworth every morning.",
        targets=[SiteTargetDraft(mention="Bestworth")],
    ))
    assert result == ("https://bestworth.ng",)


def test_managed_web_provider_is_read_only(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = execution_provider_registry().get("web_research")
    capability = provider.manifest.capabilities[0]
    assert capability.scope == "web.research"
    assert capability.mode == "read" and not capability.side_effect

    async def fake_research(**_kwargs):
        return web_research.ResearchOutcome("Verified answer", (_source(),), ("brave: ok",))

    monkeypatch.setattr(
        "app.execution.providers.native.web_research.research_web", fake_research
    )
    resource = asyncio.run(provider.discover_resources(configuration={}, credential=None))[0]
    request = ExecutionRequest(
        organization_id=uuid4(), worker_id=uuid4(), agent_id=uuid4(),
        job_id=uuid4(), work_item_id=uuid4(), run_id=uuid4(),
        capability=capability, resource=resource, operation="web.research",
        input={"query": "latest update"}, correlation_id="test-research",
        idempotency_key="test-research",
    )
    result = asyncio.run(provider.execute(request=request, configuration={}, credential=None))
    assert result.status == "success"
    assert isinstance(result.output, dict)
    assert result.output["sources"] == ["https://example.org/story"]


def test_same_url_content_change_counts_as_progress() -> None:
    prior = {"url": "https://example.org", "visibleText": "Slide one", "elements": []}
    after = {"url": "https://example.org", "visibleText": "Slide two", "elements": []}
    observations = [
        {"scope": "browser.page.read", "browserObservation": prior},
        {"scope": "browser.element.click", "browserObservation": after},
    ]
    assert not _last_browser_action_made_no_progress(observations)
    observations[-1]["browserObservation"] = prior
    assert _last_browser_action_made_no_progress(observations)
