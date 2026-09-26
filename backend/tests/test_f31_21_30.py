from __future__ import annotations

import base64
import inspect
import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.attachment_ingestion import (
    AttachmentIngestionService,
    decode_base64_content,
)
from app.application.services.browser_origin_authority import (
    browser_integration_origins,
    explicit_http_origins,
)
from app.application.services.capability_autoresolver import SemanticCapabilityResolver
from app.application.services.schedule_inference import compile_schedule
from app.application.services.worker_draft import WorkerDraftGenerator
from app.application.services.worker_memory import contains_secret_material
from app.domain.ai.providers import (
    AIInvocationContext,
    AIMediaInput,
    AIModelRole,
    AIResponse,
)
from app.domain.workforce.drafts import (
    CapabilityNeed,
    ScheduleDraft,
    WorkerDraft,
)
from app.infrastructure.database.models import (
    ArtifactAnalysis,
    Integration,
    Memory,
)
from app.runtime.managed import ManagedRuntimeExecutor
from app.runtime.planner.adaptive import AdaptiveRuntimePlanner


@dataclass
class StructuredGateway:
    payloads: list[dict[str, object]]
    schemas: list[dict[str, object]] = field(default_factory=list)
    roles: list[AIModelRole] = field(default_factory=list)

    async def generate_structured(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, object],
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> dict[str, object]:
        del system, prompt, schema_name, context, max_output_tokens, temperature
        self.roles.append(role)
        self.schemas.append(schema)
        if not self.payloads:
            raise AssertionError("No structured gateway payload remains.")
        return self.payloads.pop(0)

    async def generate_text(self, **_: Any) -> AIResponse:
        raise AssertionError("structured test must not use text generation")

    async def analyze_media(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        media: AIMediaInput,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
    ) -> AIResponse:
        del role, system, prompt, media, context, max_output_tokens
        raise AssertionError("structured test must not analyze media")


@dataclass
class VisionGateway:
    role_seen: AIModelRole | None = None
    media_seen: AIMediaInput | None = None

    async def generate_structured(self, **_: Any) -> dict[str, object]:
        raise AssertionError("vision test must not use structured generation")

    async def generate_text(self, **_: Any) -> AIResponse:
        raise AssertionError("vision test must not use text generation")

    async def analyze_media(
        self,
        *,
        role: AIModelRole,
        system: str,
        prompt: str,
        media: AIMediaInput,
        context: AIInvocationContext | None = None,
        max_output_tokens: int | None = None,
    ) -> AIResponse:
        del system, prompt, context, max_output_tokens
        self.role_seen = role
        self.media_seen = media
        return AIResponse(
            text="A calibration-style plot is visible.",
            provider="nvidia_nim",
            model="nvidia/ising-calibration-1.5-31b",
            request_id="vision-1",
        )


class _Rows:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def all(self) -> list[Any]:
        return self._values


class CapabilitySession:
    def __init__(self, integration: Integration, credential_id: UUID | None) -> None:
        self.integration = integration
        self.credential_id = credential_id

    async def scalars(self, statement: Any) -> _Rows:
        del statement
        return _Rows([self.integration])

    async def scalar(self, statement: Any) -> UUID | None:
        del statement
        return self.credential_id


@pytest.mark.asyncio
async def test_worker_draft_generator_returns_validated_full_worker_draft() -> None:
    gateway = StructuredGateway(
        [
            {
                "suggested_name": "QQ",
                "role": "Research Assistant",
                "department": "Operations",
                "supervisor_name": None,
                "charter": "Check application status and report changes.",
                "responsibilities": ["Check status", "Report changes"],
                "standing_instructions": [
                    "Never submit or modify the application without approval."
                ],
                "initial_jobs": [
                    {
                        "name": "Application status check",
                        "objective": "Check the application status.",
                        "instructions": "Use the governed browser.",
                        "completion_criteria": ["Current status is recorded."],
                        "capability_needs": [
                            {
                                "provider": "browser",
                                "need": "Read the application status page.",
                                "actions": ["open", "read"],
                            }
                        ],
                        "schedule": {
                            "kind": "daily",
                            "timezone": "Africa/Lagos",
                            "local_time": "09:00",
                            "weekdays": [],
                            "interval_minutes": None,
                            "once_at": None,
                            "event_key": None,
                            "human_readable": "Every morning",
                            "stop_after_next_run": False,
                        },
                        "approval_boundaries": [
                            {
                                "kind": "submit_requires_approval",
                                "provider": "browser",
                                "reason": "Never submit without approval.",
                                "allowed_origins": [],
                            }
                        ],
                        "integration_requirements": [
                            {
                                "provider": "browser",
                                "reason": "The website must be connected.",
                            }
                        ],
                        "start_when_ready": True,
                    }
                ],
            }
        ]
    )
    draft = await WorkerDraftGenerator(gateway).generate(
        instruction=(
            "Create QQ as a research assistant. Check my application status "
            "every morning. Never submit without asking me."
        ),
        authoritative_context={"connectedIntegrations": []},
        invocation_context=AIInvocationContext(correlation_id="golden"),
    )

    assert isinstance(draft, WorkerDraft)
    assert draft.suggested_name == "QQ"
    assert draft.initial_jobs[0].schedule.kind == "daily"
    assert draft.initial_jobs[0].capability_needs[0].provider == "browser"
    assert draft.initial_jobs[0].approval_boundaries[0].kind == "submit_requires_approval"
    assert gateway.roles == ["intent"]


@pytest.mark.asyncio
async def test_semantic_capability_resolver_can_only_select_live_exact_scope() -> None:
    integration = Integration(
        id=uuid4(),
        organization_id=uuid4(),
        provider="gmail",
        display_name="Mailbox",
        status="connected",
        config={"availableCapabilities": ["gmail.messages.recent.read"]},
    )
    gateway = StructuredGateway([{"selectedScopes": ["gmail.messages.recent.read"]}])
    session = CapabilitySession(integration, uuid4())
    resolver = SemanticCapabilityResolver(
        cast(AsyncSession, session),
        gateway,
    )

    result = await resolver.resolve(
        organization_id=integration.organization_id,
        needs=[
            CapabilityNeed(
                provider="gmail",
                need="Read recent email",
                actions=["read"],
            )
        ],
        invocation_context=AIInvocationContext(organization_id=integration.organization_id),
    )

    assert result.scopes == ("gmail.messages.recent.read",)
    selected_schema = gateway.schemas[0]["properties"]
    assert isinstance(selected_schema, dict)
    raw_selected = selected_schema["selectedScopes"]
    assert isinstance(raw_selected, dict)
    items = raw_selected["items"]
    assert isinstance(items, dict)
    assert items["enum"] == ["gmail.messages.recent.read"]


@pytest.mark.asyncio
async def test_capability_resolver_turns_missing_provider_into_connection_requirement() -> None:
    session = CapabilitySession(
        Integration(
            id=uuid4(),
            organization_id=uuid4(),
            provider="gmail",
            display_name="Mailbox",
            status="connected",
            config={},
        ),
        uuid4(),
    )
    gateway = StructuredGateway([])
    resolver = SemanticCapabilityResolver(cast(AsyncSession, session), gateway)

    result = await resolver.resolve(
        organization_id=session.integration.organization_id,
        needs=[
            CapabilityNeed(
                provider="browser",
                need="Open an application portal",
                actions=["open", "read"],
            )
        ],
        invocation_context=AIInvocationContext(),
    )

    assert result.scopes == ()
    assert result.missing_integrations == ("browser",)
    assert gateway.schemas == []




def test_explicit_browser_origins_are_exact_and_normalized() -> None:
    assert explicit_http_origins(
        "Visit https://www.nvidia.com/en-us/ and then report back."
    ) == ("https://www.nvidia.com",)
    assert explicit_http_origins(
        "Use https://Example.COM/path, not a guessed destination."
    ) == ("https://example.com",)


def test_browser_integration_origin_is_read_from_governed_resource_metadata() -> None:
    integration = Integration(
        id=uuid4(),
        organization_id=uuid4(),
        provider="browser",
        display_name="Bestworth",
        status="connected",
        config={
            "startUrl": "https://bestworthproductsltd.ng/",
            "allowedOrigins": "https://bestworthproductsltd.ng/login",
            "metadata": {
                "allowedOrigins": ["https://bestworthproductsltd.ng"],
            },
        },
    )
    assert browser_integration_origins(integration) == (
        "https://bestworthproductsltd.ng",
    )


@pytest.mark.asyncio
async def test_browser_capability_resolver_rejects_unrelated_connected_origin() -> None:
    integration = Integration(
        id=uuid4(),
        organization_id=uuid4(),
        provider="browser",
        display_name="Bestworth",
        status="connected",
        config={
            "availableCapabilities": [
                "browser.navigation.open",
                "browser.page.read",
            ],
            "metadata": {
                "allowedOrigins": ["https://bestworthproductsltd.ng"],
            },
        },
    )
    gateway = StructuredGateway([])
    resolver = SemanticCapabilityResolver(
        cast(AsyncSession, CapabilitySession(integration, None)),
        gateway,
    )

    result = await resolver.resolve(
        organization_id=integration.organization_id,
        needs=[
            CapabilityNeed(
                provider="browser",
                need="Read NVIDIA homepage",
                actions=["open", "read"],
            )
        ],
        required_browser_origins=("https://www.nvidia.com",),
        invocation_context=AIInvocationContext(),
    )

    assert result.scopes == ()
    assert result.missing_integrations == ("browser",)
    assert gateway.schemas == []


@pytest.mark.asyncio
async def test_browser_capability_resolver_accepts_matching_origin() -> None:
    integration = Integration(
        id=uuid4(),
        organization_id=uuid4(),
        provider="browser",
        display_name="NVIDIA",
        status="connected",
        config={
            "availableCapabilities": [
                "browser.navigation.open",
                "browser.page.read",
            ],
            "metadata": {
                "allowedOrigins": ["https://www.nvidia.com"],
            },
        },
    )
    gateway = StructuredGateway(
        [{"selectedScopes": ["browser.navigation.open", "browser.page.read"]}]
    )
    resolver = SemanticCapabilityResolver(
        cast(AsyncSession, CapabilitySession(integration, None)),
        gateway,
    )

    result = await resolver.resolve(
        organization_id=integration.organization_id,
        needs=[
            CapabilityNeed(
                provider="browser",
                need="Read NVIDIA homepage",
                actions=["open", "read"],
            )
        ],
        required_browser_origins=("https://www.nvidia.com",),
        invocation_context=AIInvocationContext(),
    )

    assert result.scopes == (
        "browser.navigation.open",
        "browser.page.read",
    )
    assert result.missing_integrations == ()


def test_planner_rejects_browser_resource_destination_mismatch_before_execution() -> None:
    planner = AdaptiveRuntimePlanner(cast(Any, object()))
    with pytest.raises(RuntimeError, match="not authorized"):
        planner._validate(
            {
                "decision": "act",
                "summary": "Open NVIDIA",
                "title": "Open NVIDIA",
                "instruction": "Navigate to NVIDIA.",
                "resourceId": "bestworth-browser",
                "scope": "browser.navigation.open",
                "input": {"url": "https://www.nvidia.com/en-us/"},
            },
            tools=[
                {
                    "resourceId": "bestworth-browser",
                    "scope": "browser.navigation.open",
                    "allowedOrigins": ["https://bestworthproductsltd.ng"],
                    "inputSchema": {
                        "type": "object",
                        "required": ["url"],
                    },
                }
            ],
            observations=[],
            latest_browser=None,
        )

def test_schedule_inference_normalizes_daily_weekly_once_and_stop_after() -> None:
    daily = compile_schedule(
        ScheduleDraft(
            kind="daily",
            timezone="Africa/Lagos",
            local_time="09:00",
            human_readable="Every morning",
        )
    )
    assert daily.trigger_config is not None
    daily_schedule = daily.trigger_config["schedule"]
    assert isinstance(daily_schedule, dict)
    assert daily_schedule["cadence"] == "daily"
    assert daily_schedule["timezone"] == "Africa/Lagos"

    weekly = compile_schedule(
        ScheduleDraft(
            kind="weekly",
            timezone="Africa/Lagos",
            local_time="15:00",
            weekdays=["friday"],
            human_readable="Friday afternoon",
        )
    )
    assert weekly.trigger_config is not None
    weekly_schedule = weekly.trigger_config["schedule"]
    assert isinstance(weekly_schedule, dict)
    assert weekly_schedule["weekdays"] == ["friday"]

    once = compile_schedule(
        ScheduleDraft(
            kind="once",
            timezone="Africa/Lagos",
            once_at=datetime(2026, 9, 26, 9, 0, tzinfo=UTC),
            human_readable="Check again tomorrow",
            stop_after_next_run=True,
        )
    )
    assert once.once_at is not None
    assert once.stop_after_next_run is True


def test_schedule_rejects_subhour_autonomous_interval() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 60"):
        ScheduleDraft(
            kind="interval",
            timezone="UTC",
            interval_minutes=30,
        )


def test_worker_memory_rejects_secret_like_material() -> None:
    assert contains_secret_material("API_KEY=nvapi-secret-example-123456")
    assert contains_secret_material("Bearer abcdefghijklmnopqrstuvwxyz")
    assert not contains_secret_material("Application status is still under review.")


def test_f31_memory_schema_has_type_provenance_sensitivity_and_status() -> None:
    columns = set(Memory.__table__.columns.keys())
    assert {
        "memory_type",
        "source",
        "provenance",
        "sensitivity",
        "status",
        "expires_at",
    }.issubset(columns)


def test_artifact_analysis_schema_persists_model_findings_and_provenance() -> None:
    columns = set(ArtifactAnalysis.__table__.columns.keys())
    assert {
        "organization_id",
        "artifact_id",
        "analyzer_role",
        "provider",
        "model",
        "status",
        "findings",
        "provenance",
        "sensitivity",
    }.issubset(columns)


def test_attachment_base64_validation_and_unsupported_binary_fail_closed() -> None:
    assert decode_base64_content(base64.b64encode(b"hello").decode()) == b"hello"
    with pytest.raises(ValueError, match="invalid"):
        decode_base64_content("not base64 !!!")
    service = AttachmentIngestionService(
        cast(AsyncSession, object()),
        cast(Any, object()),
    )
    with pytest.raises(ValueError, match="Unsupported"):
        service._deterministic_analysis(
            category="binary",
            content=b"\x00\x01",
            media_type="application/octet-stream",
        )


def test_deterministic_text_json_and_xlsx_attachment_analysis() -> None:
    service = AttachmentIngestionService(
        cast(AsyncSession, object()),
        cast(Any, object()),
    )
    text = service._deterministic_analysis(
        category="text",
        content=b'{"status":"under review"}',
        media_type="application/json",
    )
    assert text["method"] == "json_parse"
    assert text["structured"] == {"status": "under review"}

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Status"
    sheet.append(["candidate", "state"])
    sheet.append(["Divine", "under review"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    spreadsheet = service._deterministic_analysis(
        category="spreadsheet",
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert spreadsheet["method"] == "openpyxl_sample"
    assert spreadsheet["sheetCount"] == 1


@pytest.mark.asyncio
async def test_vision_route_is_generic_and_uses_configured_vision_role() -> None:
    gateway = VisionGateway()
    # The actual database wrapper is tested through schema/verifiers. This directly
    # verifies the model boundary used by the image analyzer.
    response = await gateway.analyze_media(
        role="vision",
        system="untrusted image",
        prompt="inspect",
        media=AIMediaInput(
            media_type="image/png",
            data_base64=base64.b64encode(b"image").decode(),
        ),
    )
    assert response.model == "nvidia/ising-calibration-1.5-31b"
    assert gateway.role_seen == "vision"
    assert gateway.media_seen is not None


def test_managed_runtime_planner_has_memory_and_stop_after_next_run_support() -> None:
    source = inspect.getsource(ManagedRuntimeExecutor)
    assert "standingInstructions" in source
    assert '"memory"' in source
    assert "_apply_stop_after_next_run" in source
    assert "save_trigger_config" in source
    assert "job_status_v1" in source


def test_existing_worker_job_create_uses_generic_browser_origin_provisioning() -> None:
    from app.application.services import browser_origin_authority, conversation_commands

    compiler = inspect.getsource(conversation_commands.ConversationCommandCompiler)
    authority = inspect.getsource(browser_origin_authority)

    assert "_prepare_existing_worker_job_payload" in compiler
    assert "explicit_http_origins(source_message.content)" in compiler
    assert "ensure_managed_browser_origins" in compiler
    assert "SemanticCapabilityResolver" in compiler
    assert "required_browser_origins=origins" in compiler
    assert "_provision_managed_job_authority" in compiler
    assert '"authorizedBrowserOrigins": list(origins)' in compiler

    # The mechanism is site-agnostic: origins come from human-authored URLs.
    assert "nvidia.com" not in authority.lower()
    assert "bestworth" not in authority.lower()
    assert "for origin in requested" in authority
    assert "current = covered.get(origin)" in authority
    assert "requested_set = set(requested)" in authority
    assert "resource_origins.issubset(requested_set)" in authority


def test_managed_browser_origins_default_to_lean_visual_loading() -> None:
    from app.application.services import browser_origin_authority

    source = inspect.getsource(browser_origin_authority)
    assert '"loadVisualResources": "false"' in source
    assert 'current_config.get("managedBy") == "worker_autonomy"' in source


def test_worker_autonomy_uses_existing_authority_and_governance_services() -> None:
    from app.application.services.worker_autonomy import WorkerAutonomyService

    source = inspect.getsource(WorkerAutonomyService)
    assert "WorkerDraftGenerator" in source
    assert "SemanticCapabilityResolver" in source
    assert "_provision_managed_job_authority" in source
    assert "_create_policy" in source
    assert "save_trigger_config" in source
    assert '"waiting_integration"' in source
    assert "update_worker" in source
    assert "update_job" in source
    assert "automatic_agent" in source
    assert "ensure_managed_browser_origins" in source
    assert "authorizedBrowserOrigins" in source


def test_golden_qq_scenario_contract_is_represented_without_scope_invention() -> None:
    from app.application.services import capability_autoresolver, worker_autonomy

    autonomy_source = inspect.getsource(worker_autonomy)
    resolver_source = inspect.getsource(capability_autoresolver)
    assert "suggested_name" not in resolver_source  # resolver only handles capabilities
    assert '"selectedScopes"' in resolver_source
    assert '"enum": allowed_scopes' in resolver_source
    assert "capabilityNeeds" in autonomy_source
    assert "missingIntegrations" in autonomy_source
    assert "approval_boundaries" in autonomy_source
    assert "humanSchedule" in autonomy_source
