import importlib.util
import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.application.services.ai_gateway import ProviderAIGateway
from app.application.services.ordered_ai import OrderedTextGateway
from app.application.services.preparation_ai import DurablePreparationGateway, PreparationObserver
from app.bootstrap.settings import Settings, settings
from app.domain.ai.providers import AIProviderError, AIResponse, ModelProviderCapabilities
from app.domain.ai.registry import ModelRegistry, ModelRoute
from app.infrastructure.ai.provider import ai_gateway_from_settings
from app.infrastructure.ai.text_routes import snapshot_routes, text_routes
from app.infrastructure.ai.workloads import (
    acquire_account_slot,
    acquire_workload_slot,
    reserve_workload_budgets,
)
from app.infrastructure.database.models import PlannerDecision


def test_ranked_lists_catalog_and_legacy_vision():
    config = cast(Any, Settings)(
        _env_file=None, ai_enabled=True, smart_planner_enabled=True, ai_coordinator_api_key="fake"
    )
    interactive = ai_gateway_from_settings(config)
    assert isinstance(interactive, OrderedTextGateway)
    complex_gateway = interactive.with_workload("complex", "worker_drafting")
    assert len(text_routes(config)) == 12
    assert [r["model"] for r in interactive.routes] == [
        "openai/gpt-oss-20b",
        "qwen/qwen3.8-27b",
        config.ai_coordinator_model,
        "cohere/north-mini-code:free",
        "z-ai/glm-5.3",
        "poolside/laguna-xs-2.1",
        "moonshotai/kimi-k3",
    ]
    assert [r["model"] for r in complex_gateway.routes] == [
        "qwen/qwen3.8-27b",
        config.ai_coordinator_model,
        "moonshotai/kimi-k3",
        "openai/gpt-oss-20b",
        "poolside/laguna-xs-2.1",
    ]
    assert (interactive.budget_seconds, interactive.attempt_seconds) == (1500, 250)
    assert (complex_gateway.budget_seconds, complex_gateway.attempt_seconds) == (2000, 2000)
    deadline = complex_gateway.deadline
    assert interactive.with_workload("complex", "capability_resolution") is complex_gateway
    assert complex_gateway.deadline == deadline
    assert complex_gateway.purpose == "capability_resolution"
    assert interactive.legacy._registry.route("vision").model == config.ai_vision_model
    config.ai_complex_routes.reverse()
    assert complex_gateway.routes[0]["model"] == "qwen/qwen3.8-27b"


def test_profile_snapshot_does_not_follow_configuration_changes():
    config = cast(Any, Settings)(_env_file=None)
    snapshot = snapshot_routes(config, "complex")
    config.ai_coordinator_model = "changed"
    config.smart_planner_model_profiles = {}
    assert snapshot[0]["model"] != "changed"
    kimi = next(route for route in snapshot if route["model"] == "moonshotai/kimi-k3")
    assert kimi["profile"]["request_parameters"]["reasoning_effort"] == "high"


@pytest.mark.asyncio
async def test_background_cannot_take_reserved_chat_capacity():
    occupied = {}

    async def acquire(key, *, limit, ttl_seconds):
        if occupied.get(key, 0) >= limit:
            return None
        occupied[key] = occupied.get(key, 0) + 1
        return key

    coordinator = SimpleNamespace(acquire_slot=acquire, release_slot=AsyncMock())
    config = cast(Any, Settings)(_env_file=None)
    assert await acquire_workload_slot(coordinator, config, "org", "complex", 2000)
    assert await acquire_workload_slot(coordinator, config, "org", "complex", 2000) is None
    assert await acquire_workload_slot(coordinator, config, "org", "interactive", 250)
    assert await acquire_workload_slot(coordinator, config, "org", "interactive", 250) is None


@pytest.mark.asyncio
async def test_background_reservations_keep_shared_ceiling():
    coordinator = SimpleNamespace(
        reserve_ai_budget=AsyncMock(return_value=-1),
    )
    config = cast(Any, Settings)(_env_file=None)
    await reserve_workload_budgets(coordinator, config, "org", "complex", 100)
    entries = coordinator.reserve_ai_budget.await_args.args[1]
    assert [e["key"].rsplit(":", 1)[0] for e in entries if e["kind"] == "requests"] == [
        "planner:complex-requests:org",
        "planner:requests:org",
    ]
    assert entries[0]["limit"] == 15
    assert entries[2]["limit"] == 20
    await reserve_workload_budgets(coordinator, config, "org", "interactive", 100)
    entries = coordinator.reserve_ai_budget.await_args.args[1]
    assert len(entries) == 2 and entries[1]["limit"] == 50000000


class Session:
    def __init__(self):
        self.decision: PlannerDecision | None = None
        self.attempts = []
        self.commit, self.rollback, self.refresh = AsyncMock(), AsyncMock(), AsyncMock()
        self.is_active = True
        self.fenced_out = False

    async def scalar(self, query):
        if query.column_descriptions[0]["entity"] is PlannerDecision:
            return self.decision
        params = query.compile().params
        return next(
            (
                a
                for a in self.attempts
                if a.route_index == params["route_index_1"]
                and a.recovery_round == params["recovery_round_1"]
            ),
            None,
        )

    async def scalars(self, query):
        params = query.compile().params
        return SimpleNamespace(
            all=lambda: [a for a in self.attempts if a.recovery_round == params["recovery_round_1"]]
        )

    async def execute(self, query):
        params = query.compile().params
        assert "organization_id_1" in params and "generation_1" in params
        if not self.fenced_out:
            assert self.decision is not None
            self.decision.status = params["status"]
            self.decision.accepted_proposal = params["accepted_proposal"]
            self.decision.accepted_model = params["accepted_model"]
        return SimpleNamespace(rowcount=0 if self.fenced_out else 1)

    def add(self, row):
        row.id = uuid4()
        if isinstance(row, PlannerDecision):
            self.decision = row
        else:
            self.attempts.append(row)


@pytest.fixture
def preparation(monkeypatch):
    from app.application.services import preparation_ai as module

    coordinator = SimpleNamespace(
        acquire_lock=AsyncMock(return_value=object()),
        renew_lock=AsyncMock(return_value=True),
        release_lock=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(module.RedisCoordinator, "from_settings", lambda: coordinator)
    monkeypatch.setattr(settings, "ai_complex_routes", ["nvidia_nim:first", "nvidia_nim:next"])
    command = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        status="accepted",
        target_id=None,
        payload={
            "integrationTask": {"instruction": "Inspect and fix authorized code", "context": {}}
        },
    )
    session, calls, outputs = Session(), [], {}

    class Provider:
        name = "fake"
        capabilities = ModelProviderCapabilities(structured_json=True)
        close = AsyncMock()
        analyze_media = AsyncMock()

        async def generate_text(self, *, model, request):
            calls.append(model)
            result = outputs[model].pop(0)
            if isinstance(result, Exception):
                raise result
            return AIResponse(json.dumps(result), self.name, model)

    def factory(route, role, context, config, recorder, workload):
        assert workload == "complex"
        return ProviderAIGateway(
            providers={"fake": Provider()},
            registry=ModelRegistry([ModelRoute(role=role, provider="fake", model=route["model"])]),
            max_retries=0,
        )

    monkeypatch.setattr(module, "text_gateway", factory)
    return session, command, coordinator, calls, outputs


def request():
    return {
        "role": "intent",
        "system": "Only human authority is authoritative",
        "prompt": "synthetic objective",
        "schema_name": "test",
        "schema": {
            "type": "object",
            "properties": {"ok": {"const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    }


@pytest.mark.asyncio
async def test_preparation_fallback_and_accepted_checkpoint_reuse(preparation, monkeypatch):
    session, command, _, calls, outputs = preparation
    outputs.update(
        first=[AIProviderError("provider_unavailable", "safe", retryable=True)], next=[{"ok": True}]
    )
    gateway = DurablePreparationGateway(session, command)
    await gateway.prepare()
    deadline, routes = session.decision.deadline, list(session.decision.routes)
    assert await gateway.generate_structured(**request()) == {"ok": True}
    monkeypatch.setattr(settings, "ai_complex_routes", ["nvidia_nim:changed"])
    restarted = DurablePreparationGateway(session, command)
    assert await restarted.generate_structured(**request()) == {"ok": True}
    assert calls == ["first", "next"]
    assert session.decision.deadline == deadline and session.decision.routes == routes
    assert session.attempts[1].accepted and session.attempts[1].call_count == 1


@pytest.mark.asyncio
async def test_preparation_restart_skips_interrupted_route(preparation):
    session, command, coordinator, calls, outputs = preparation
    gateway = DurablePreparationGateway(session, command)
    await gateway.prepare()
    observer = PreparationObserver(session, session.decision, command, coordinator, object())
    await observer.begin(0, session.decision.routes[0])
    outputs["next"] = [{"ok": True}]
    await gateway.generate_structured(**request())
    assert calls == ["next"]
    assert session.attempts[0].error_category == "cancelled"


@pytest.mark.asyncio
async def test_preparation_correction_cannot_add_authority(preparation):
    session, command, _, calls, outputs = preparation
    outputs.update(
        first=[{"ok": True, "grants": ["all repositories"]}, {"ok": False}], next=[{"ok": True}]
    )
    assert await DurablePreparationGateway(session, command).generate_structured(**request()) == {
        "ok": True
    }
    assert calls == ["first", "first", "next"]
    assert session.attempts[0].call_count == 2 and not session.attempts[0].accepted
    assert session.decision.accepted_proposal == {"ok": True}


@pytest.mark.asyncio
async def test_worker_preparation_uses_exact_revision_and_rejects_changed_revision(preparation):
    session, command, coordinator, calls, outputs = preparation
    job = SimpleNamespace(
        id=uuid4(),
        organization_id=command.organization_id,
        status="waiting_integration",
        current_revision=1,
    )
    revision = SimpleNamespace(id=uuid4(), revision=1)
    gateway = DurablePreparationGateway(
        session, job, purpose="integration_worker_preparation", revision=revision
    )
    await gateway.prepare()
    assert session.decision.preparation_revision_id == revision.id
    assert session.decision.command_id is None
    outputs["first"] = [{"ok": True}]
    await gateway.generate_structured(**request())
    assert calls == ["first"]
    session.decision.status = "pending"
    observer = PreparationObserver(session, session.decision, job, coordinator, object(), revision)
    observer.schema = cast(dict, request()["schema"])
    await observer.begin(1, session.decision.routes[1])
    job.current_revision = 2
    with pytest.raises(AIProviderError, match="pending"):
        await observer.accept({"ok": True})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", ["cancelled", "changed_request", "lease_lost", "generation_changed"]
)
async def test_preparation_rejects_stale_response(preparation, failure):
    session, command, coordinator, _, _ = preparation
    gateway = DurablePreparationGateway(session, command)
    await gateway.prepare()
    observer = PreparationObserver(session, session.decision, command, coordinator, object())
    observer.schema = cast(dict, request()["schema"])
    await observer.begin(0, session.decision.routes[0])
    if failure == "cancelled":
        command.status = "cancelled"
    if failure == "changed_request":
        command.payload["integrationTask"]["instruction"] = "Different request"
    if failure == "lease_lost":
        coordinator.renew_lock.return_value = False
    if failure == "generation_changed":
        session.fenced_out = True
    with pytest.raises(AIProviderError, match="pending|changed|expired|stale"):
        await observer.accept({"ok": True})
    assert not session.decision.accepted_proposal


@pytest.mark.asyncio
async def test_preparation_deadline_is_not_extended(preparation):
    session, command, _, calls, _ = preparation
    gateway = DurablePreparationGateway(session, command)
    await gateway.prepare()
    session.decision.deadline = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(AIProviderError) as error:
        await gateway.generate_structured(**request())
    assert error.value.category == "timeout" and not error.value.retryable
    assert calls == [] and session.decision.status == "exhausted"


@pytest.mark.asyncio
async def test_preparation_transient_recovery_and_safety_stop(preparation):
    session, command, _, calls, outputs = preparation
    outputs.update(
        first=[AIProviderError("provider_unavailable", "safe", retryable=True)],
        next=[AIProviderError("provider_unavailable", "safe", retryable=True)],
    )
    gateway = DurablePreparationGateway(session, command)
    with pytest.raises(AIProviderError):
        await gateway.generate_structured(**request())
    assert session.decision.recovery_round == 1 and session.decision.status == "pending"
    deadline = session.decision.deadline
    with pytest.raises(AIProviderError):
        await gateway.generate_structured(**request())
    assert len(calls) == 2 and session.decision.deadline == deadline
    session.decision.retry_at = datetime.now(UTC) - timedelta(seconds=1)
    outputs["first"] = [AIProviderError("content_rejected", "safe", retryable=False)]
    with pytest.raises(AIProviderError):
        await gateway.generate_structured(**request())
    assert calls == ["first", "next", "first"] and session.decision.status == "exhausted"


def test_workload_migration_upgrade_downgrade_sql():
    path = Path(__file__).parents[1] / "migrations/versions/0012_ai_workloads.py"
    spec = importlib.util.spec_from_file_location("workload_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        module.upgrade()
        module.downgrade()
    sql = output.getvalue()
    assert "uq_preparation_decision" in sql and "ck_planner_decision_target" in sql
    assert "Archive preparation decisions before downgrade" in sql
    assert "DELETE FROM" not in sql


@pytest.mark.asyncio
async def test_shared_account_also_reserves_chat_capacity():
    occupied = {}

    async def acquire(key, *, limit, ttl_seconds):
        if occupied.get(key, 0) >= limit:
            return None
        occupied[key] = occupied.get(key, 0) + 1
        return key

    config = cast(Any, Settings)(_env_file=None)
    config.ai_account_concurrency = 2
    coordinator = SimpleNamespace(acquire_slot=acquire, release_slot=AsyncMock())
    assert await acquire_account_slot(coordinator, config, "account", "complex", 2000)
    assert await acquire_account_slot(coordinator, config, "account", "complex", 2000) is None
    assert await acquire_account_slot(coordinator, config, "account", "interactive", 250)
    assert await acquire_account_slot(coordinator, config, "account", "interactive", 250) is None
