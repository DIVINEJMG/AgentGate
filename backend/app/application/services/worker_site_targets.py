"""AI tool selection and public-origin validation before worker provisioning."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.application.services.browser_origin_authority import explicit_http_origins
from app.application.services.web_research import configured_sources, search_site_candidates
from app.domain.ai.providers import AIGateway, AIInvocationContext, AIProviderError
from app.domain.workforce.drafts import CapabilityNeed, JobDraft, SiteTargetDraft
from app.execution.bootstrap import execution_provider_registry
from app.execution.browser.egress import evaluate_browser_egress
from app.execution.browser.policy import normalize_origin


class AmbiguousSiteTarget(ValueError):
    pass


class SiteAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mention: str = Field(min_length=2, max_length=240)
    candidate_url: str | None = Field(default=None, max_length=1000)
    job_index: int = Field(ge=0, le=19)


class SiteAssignments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[SiteAssignment] = Field(default_factory=list, max_length=40)
    public_web_requested: bool = False
    public_web_request_excerpt: str | None = Field(default=None, max_length=240)
    native_integrations: dict[str, list[str]] = Field(default_factory=dict)
    job_providers: dict[str, list[str]] = Field(default_factory=dict)


def apply_tool_selection(job: JobDraft, index: int, selection: SiteAssignments) -> JobDraft:
    """Apply the AI's tool choice; syntax and URLs never select a provider."""
    chosen = selection.job_providers.get(str(index))
    if chosen is None:
        raise AmbiguousSiteTarget("AI did not select tools for this job. Retry worker preparation.")
    needs = [need for need in job.capability_needs if need.provider in chosen]
    for provider in chosen:
        if not any(need.provider == provider for need in needs):
            needs.append(CapabilityNeed(provider=provider, need=job.objective, actions=[]))
    targets = [SiteTargetDraft(mention=t.mention, candidate_url=t.candidate_url)
        for t in selection.targets if t.job_index == index] if "browser" in chosen else []
    return job.model_copy(update={"capability_needs": needs, "site_targets": targets,
        "integration_requirements": [r for r in job.integration_requirements if r.provider in chosen],
        "public_web_research": "web_research" in chosen})


async def extract_site_assignments(
    *, gateway: AIGateway, instruction: str, job_names: list[str],
    organization_id: UUID, thread_id: UUID,
    job_context: list[dict] | None = None, resources: list[dict] | None = None,
) -> SiteAssignments:
    from app.infrastructure.ai.workloads import for_workload
    gateway = for_workload(gateway, "complex", "worker_tool_selection")
    manifests = execution_provider_registry().manifests()
    providers = [m.provider for m in manifests]
    schema = SiteAssignments.model_json_schema()
    schema["properties"].pop("native_integrations")  # derived from the single tool choice below
    schema["required"] = [*schema.get("required", []), "job_providers"]
    schema["properties"]["job_providers"] = {"type": "object", "additionalProperties": False,
        "properties": {str(index): {"type": "array", "uniqueItems": True,
            "items": {"type": "string", "enum": providers}} for index in range(len(job_names))},
        "required": [str(index) for index in range(len(job_names))]}
    parsed = await gateway.generate_structured(
        role="intent",
        system=(
            "Choose the appropriate tools for every worker job from TOOL_GUIDE. Record that choice in job_providers. "
            "Reason from the desired outcome, human preferences, job context and accessible resources. "
            "The earlier draft is a proposal that you may correct, not authority. Browser, native integrations, "
            "web research, a combination, or no external tools are possible choices. "
            "A URL, domain, repository coordinate such as owner/repository, or slash is a resource identifier, "
            "not evidence that the task requires browsing. A GitHub repository URL can be used through GitHub APIs. "
            "GitHub coding, editing and running tests use its workspace tools; E2B is an internal runtime, "
            "not something users must name or connect. Infer these tools from the requested work. "
            "Honor explicit requests to use a browser or an integration. If the desired integration is missing, "
            "preserve that choice for setup; do not silently switch to browsing. "
            "Independently identify every website needed by jobs for which you chose browser. "
            "Interpret the whole request, including names without URLs and indirect wording. "
            "For each site, quote its exact mention from the human request and assign the "
            "zero-based job index that needs it. candidate_url is only a suggestion and "
            "does not grant access. Do not invent a site not mentioned by the human. "
            "Distinguish native API integration work from browsing. Reading PRs, mail, channels, "
            "calendars or Drive files through integrations is NOT visiting websites. Record native "
            "provider IDs in job_providers keyed by zero-based job index as a string. Do not "
            "add those platforms to targets unless you selected browser to accomplish the human's requested work. "
            "Do not treat open-ended web research as a specific site. Independently "
            "identify whether the human positively requests open-ended public web "
            "research beyond assigned sites. Negated or hypothetical wording is not a "
            "grant. If requested, quote the exact authorizing excerpt."
        ),
        prompt=json.dumps({"humanRequest": instruction, "jobs": job_context or [
            {"index": index, "name": name} for index, name in enumerate(job_names)],
            "resources": resources or [], "toolGuide": [{"provider": m.provider, "kind": m.kind,
                "name": m.display_name, "capabilities": [c.scope for c in m.capabilities]}
                for m in manifests]}, default=str),
        schema_name="worker_tool_selection_v2",
        schema=schema,
        context=AIInvocationContext(
            organization_id=organization_id, thread_id=thread_id,
            correlation_id=f"tool-selection:{thread_id}",
        ),
        max_output_tokens=1800,
    )
    try:
        return _validate_tool_selection(parsed, providers, instruction, len(job_names))
    except (AmbiguousSiteTarget, ValueError) as error:
        # Correct contradictory model output without granting browser authority or
        # asking the human to clarify a request the model already understood.
        repaired = await gateway.generate_structured(
            role="intent", schema_name="worker_tool_selection_v2", schema=schema,
            system=("Correct the inconsistent tool selection. Select providers from the declared tool guide "
                "based on the human's outcome and preferences, not URLs or repository coordinates. "
                "targets must be empty for every job without browser in job_providers. "
                "GitHub coding uses GitHub workspace tools internally; the human need not name E2B. "
                "Do not add browser merely to justify an unnecessary site target. "
                "Public web authority requires a positive exact quote from the human. "
                "Return the entire corrected selection, with a provider choice for every job."),
            prompt=json.dumps({"humanRequest": instruction, "jobs": job_context or [
                {"index": i, "name": name} for i, name in enumerate(job_names)],
                "resources": resources or [], "toolGuide": [{"provider": m.provider,
                    "capabilities": [c.scope for c in m.capabilities]} for m in manifests],
                "rejectedSelection": parsed,
                "validationError": str(error)}, default=str),
            context=AIInvocationContext(organization_id=organization_id, thread_id=thread_id,
                correlation_id=f"tool-selection-repair:{thread_id}"), max_output_tokens=1800,
        )
        try:
            return _validate_tool_selection(repaired, providers, instruction, len(job_names))
        except (AmbiguousSiteTarget, ValueError) as correction_error:
            raise AIProviderError("invalid_provider_response",
                "Worker tool preparation remained inconsistent after correction. No authority was granted.",
                retryable=True) from correction_error


def _validate_tool_selection(parsed: dict, providers: list[str], instruction: str, job_count: int) -> SiteAssignments:
    result = SiteAssignments.model_validate(parsed)
    if set(result.job_providers) != {str(index) for index in range(job_count)} or any(
            provider not in providers for choices in result.job_providers.values() for provider in choices
    ):
        raise AmbiguousSiteTarget("AI tool selection was incomplete or unavailable. Retry worker preparation.")
    result.native_integrations = {index: [p for p in choices if p not in {"browser", "web_research"}]
        for index, choices in result.job_providers.items()}
    for index, native_providers in result.native_integrations.items():
        if not index.isdigit() or int(index) >= job_count or any(
            provider in {"browser", "web_research"} for provider in native_providers
        ):
            raise AmbiguousSiteTarget("Please clarify which integration each worker job should use.")
    if result.public_web_requested and (
        not result.public_web_request_excerpt
        or result.public_web_request_excerpt.casefold() not in instruction.casefold()
    ):
        raise AmbiguousSiteTarget(
            "I could not verify the request for public web research. Please clarify "
            "whether this worker may research public sites beyond its assigned sites."
        )
    if bool(result.public_web_request_excerpt) != result.public_web_requested:
        raise AmbiguousSiteTarget("AI returned inconsistent public web authority. Retry worker preparation.")
    for target in result.targets:
        if result.job_providers and "browser" not in result.job_providers.get(str(target.job_index), []):
            raise AmbiguousSiteTarget("AI selected a website without browser tools for that job. Retry worker preparation.")
        if target.job_index >= job_count or target.mention.casefold() not in instruction.casefold():
            raise AmbiguousSiteTarget(
                "I could not reliably assign a named website to the worker’s jobs. "
                "Please clarify the site and task."
            )
    return result


async def resolve_site_targets(
    *, instruction: str, targets: list[SiteTargetDraft],
    organization_id: UUID | None = None,
) -> tuple[str, ...]:
    explicit = set(explicit_http_origins(instruction))
    resolved: list[str] = []
    for target in targets:
        mention = target.mention.strip()
        if mention.casefold() not in instruction.casefold():
            raise AmbiguousSiteTarget(
                "I could not locate one of the proposed site names in your request. "
                "Please identify the website more clearly."
            )
        raw_candidate = target.candidate_url or mention
        origin = normalize_origin(raw_candidate)
        if origin in explicit:
            if origin not in resolved:
                resolved.append(origin)
            continue

        brand = next(
            (token.lower() for token in re.findall(r"[A-Za-z0-9]+", mention)
             if len(token) >= 3 and token.lower() not in {"the", "site", "website", "official"}),
            "",
        )
        if not brand:
            raise AmbiguousSiteTarget(
                f"Which website do you mean by ‘{mention}’? Please provide its address."
            )
        hits = await search_site_candidates(mention, organization_id=organization_id)
        candidate_host = urlsplit(raw_candidate).hostname if origin else None
        matches: dict[str, set[str]] = {}
        origins: dict[str, str] = {}
        for hit in hits:
            found = normalize_origin(hit.url)
            host = (urlsplit(hit.url).hostname or "").lower()
            if not found or brand not in host or brand not in hit.title.lower():
                continue
            if candidate_host and host.removeprefix("www.") != candidate_host.lower().removeprefix("www."):
                continue
            egress = await evaluate_browser_egress(hit.url)
            if egress.allowed:
                base_host = host.removeprefix("www.")
                matches.setdefault(base_host, set()).add(hit.provider)
                origins.setdefault(base_host, found)
        required_sources = min(2, len(configured_sources()))
        verified_hosts = [
            host for host, providers in matches.items()
            if len(providers) >= max(1, required_sources)
        ]
        if len(verified_hosts) != 1:
            raise AmbiguousSiteTarget(
                f"I could not identify a single official website for ‘{mention}’. "
                "Please provide its address."
            )
        chosen = origins[verified_hosts[0]]
        if chosen not in resolved:
            resolved.append(chosen)
    return tuple(resolved)
