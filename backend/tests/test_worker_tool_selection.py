from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.services.worker_site_targets import (
    SiteAssignments,
    apply_tool_selection,
    extract_site_assignments,
)
from app.domain.ai.providers import AIProviderError
from app.domain.workforce.drafts import CapabilityNeed, JobDraft, SiteTargetDraft


def proposed_browser_job():
    return JobDraft(name="Read pull requests", objective="Read PRs in DIVINEJMG/AgentGate",
        capability_needs=[CapabilityNeed(provider="browser", need="Read page")],
        site_targets=[SiteTargetDraft(mention="DIVINEJMG/AgentGate", candidate_url="https://github.com/DIVINEJMG/AgentGate")])


@pytest.mark.parametrize("identifier", ["DIVINEJMG/AgentGate", "https://github.com/DIVINEJMG/AgentGate"])
@pytest.mark.asyncio
async def test_ai_native_selection_corrects_browser_draft_for_repository_identifiers(identifier):
    gateway: Any = SimpleNamespace(generate_structured=AsyncMock(return_value={"job_providers": {"0": ["github"]}}))
    selection = await extract_site_assignments(gateway=gateway, instruction="Read PRs in " + identifier,
        job_names=["Read pull requests"], organization_id=uuid4(), thread_id=uuid4(),
        resources=[{"provider": "github", "name": "DIVINEJMG/AgentGate"}],
        job_context=[{"index": 0, "objective": "Read PRs", "proposedProviders": ["browser"]}])
    job = apply_tool_selection(proposed_browser_job(), 0, selection)
    assert [need.provider for need in job.capability_needs] == ["github"]
    assert job.site_targets == []
    args = gateway.generate_structured.call_args.kwargs
    assert "toolGuide" in args["prompt"] and "DIVINEJMG/AgentGate" in args["prompt"]
    assert args["schema"]["properties"]["job_providers"]["required"] == ["0"]


@pytest.mark.parametrize("choices", [["browser"], ["github", "browser"]])
def test_browser_and_mixed_choices_are_preserved_even_for_pr_tasks(choices):
    job = apply_tool_selection(proposed_browser_job(), 0, SiteAssignments.model_validate({
        "job_providers": {"0": choices}, "targets": [{"mention": "github.com", "job_index": 0}]}))
    assert {need.provider for need in job.capability_needs} == set(choices)
    assert [target.mention for target in job.site_targets] == ["github.com"]


def test_empty_selection_removes_external_tools_and_stale_sites():
    job = apply_tool_selection(proposed_browser_job(), 0, SiteAssignments(job_providers={"0": []}))
    assert not job.capability_needs and not job.site_targets


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [
    {"job_providers": {}},
    {"job_providers": {"0": ["invented"]}},
    {"job_providers": {"1": ["github"]}},
    {"job_providers": {"0": ["github"]}, "targets": [{"mention": "github.com", "job_index": 0}]},
])
async def test_unavailable_or_inconsistent_choices_are_rejected_not_substituted(response):
    gateway: Any = SimpleNamespace(generate_structured=AsyncMock(return_value=response))
    with pytest.raises(AIProviderError, match="remained inconsistent"):
        await extract_site_assignments(gateway=gateway, instruction="Read PRs in github.com",
            job_names=["Read PRs"], organization_id=uuid4(), thread_id=uuid4())
    assert gateway.generate_structured.await_count == 2


@pytest.mark.asyncio
async def test_native_coding_selection_repairs_website_target_without_adding_browser():
    gateway: Any = SimpleNamespace(generate_structured=AsyncMock(side_effect=[
        {"job_providers": {"0": ["github"]}, "targets": [
            {"mention": "DIVINEJMG/AgentGate", "job_index": 0}]},
        {"job_providers": {"0": ["github"]}, "targets": []},
    ]))
    selection = await extract_site_assignments(gateway=gateway,
        instruction="Create a coding worker for DIVINEJMG/AgentGate. Edit code, run tests and open draft PRs.",
        job_names=["Coding"], organization_id=uuid4(), thread_id=uuid4())
    assert selection.job_providers == {"0": ["github"]}
    assert selection.targets == []
    assert gateway.generate_structured.await_count == 2
    first_prompt = gateway.generate_structured.call_args_list[0].kwargs["system"]
    assert "not something users must name" in first_prompt


def test_web_research_selection_uses_only_selected_provider():
    job = apply_tool_selection(proposed_browser_job(), 0,
        SiteAssignments(job_providers={"0": ["web_research"]}))
    assert [need.provider for need in job.capability_needs] == ["web_research"]
    assert job.public_web_research and not job.site_targets
