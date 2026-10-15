"""The simulated model: only a flagged simulator connection may use a loopback address, never in
production; the launcher needs its own database; and AgentGate's real OpenAI-format transport
gets the simulator's answer, streamed or not, with the exchange recorded for replay."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.bootstrap.settings import Settings
from app.domain.ai.providers import AIProviderError, AITextRequest
from app.infrastructure.ai.connections import TextConnection
from app.infrastructure.ai.model_profiles import PlannerModelProfile
from app.infrastructure.ai.openai_chat import ConfiguredChatProvider
from scripts import ai_simulator, run_simulation

SIM = {"api_format": "openai_chat", "simulator": True, "base_url": "http://127.0.0.1:8788/v1",
       "hosting_provider": "simulator", "credential_ref": "simulator", "schema_mode": "json_schema",
       "enabled": True, "data_allowed": True}
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"],
          "additionalProperties": False}


def test_only_a_simulator_connection_may_use_a_loopback_address() -> None:
    assert TextConnection.model_validate(SIM).base_url == "http://127.0.0.1:8788/v1"
    with pytest.raises(ValidationError, match="public HTTPS"):
        TextConnection.model_validate({**SIM, "simulator": False})
    with pytest.raises(ValidationError, match="loopback"):
        TextConnection.model_validate({**SIM, "base_url": "http://10.0.0.5:8788/v1"})


def test_production_refuses_a_simulator_connection() -> None:
    config = Settings(environment="production", cors_allowed_origins=("https://app.example",),
                      ai_text_connections={"sim": TextConnection.model_validate(SIM)})
    with pytest.raises(ValueError, match="forbidden in production"):
        config.validate_security()
    Settings(environment="staging", ai_text_connections={"sim": TextConnection.model_validate(SIM)}).validate_security()


def test_the_launcher_needs_its_own_database() -> None:
    with pytest.raises(SystemExit, match="DATABASE_URL"):
        run_simulation.simulation_environment({}, 8788)
    environment = run_simulation.simulation_environment({"DATABASE_URL": "postgresql://sim"}, 8788)
    connections = json.loads(environment["AI_TEXT_CONNECTIONS"])
    assert TextConnection.model_validate(connections["sim"]).simulator
    assert json.loads(environment["SMART_PLANNER_ROUTES"]) == ["sim:claude-sim"]
    assert PlannerModelProfile.model_validate(json.loads(environment["SMART_PLANNER_MODEL_PROFILES"])["sim:claude-sim"])


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name, folder in {"ROOT": tmp_path, "PENDING": tmp_path / "pending", "ANSWERS": tmp_path / "answers",
                         "LOG": tmp_path / "log", "TRANSCRIPT": tmp_path / "transcript.jsonl"}.items():
        monkeypatch.setattr(ai_simulator, name, folder)
    ai_simulator._ensure_dirs()
    return tmp_path


def _provider(app: Any) -> ConfiguredChatProvider:
    return ConfiguredChatProvider(
        name="sim", connection=TextConnection.model_validate(SIM), credential="local-simulator",
        profile=PlannerModelProfile(context_tokens=400000, output_format="json_schema"),
        timeout_seconds=30, transport=httpx.ASGITransport(app=app))


async def _answer_when_asked(folders: Path, answer: dict[str, Any]) -> dict[str, Any]:
    while not list((folders / "pending").glob("*.json")):
        await asyncio.sleep(0.05)
    pending = next((folders / "pending").glob("*.json"))
    record = json.loads(pending.read_text(encoding="utf-8"))
    (folders / "answers" / pending.name).write_text(json.dumps(answer), encoding="utf-8")
    return record


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_agentgate_receives_the_simulated_answer(folders: Path, stream: bool) -> None:
    provider = _provider(ai_simulator.build_app(None))
    request = AITextRequest(system="You plan.", prompt="List the open issues.", response_format="json_object",
                            json_schema=SCHEMA, schema_name="decision", stream=stream)
    response, record = await asyncio.gather(
        provider.generate_text(model="claude-sim", request=request),
        _answer_when_asked(folders, {"content": {"answer": "ok"}}))
    assert json.loads(response.text) == {"answer": "ok"}
    assert record["schema_name"] == "decision" and record["schema"] == SCHEMA
    assert record["messages"][-1]["content"] == "List the open issues."
    entry = json.loads((folders / "transcript.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert entry["answer"] == {"content": {"answer": "ok"}}
    assert not list((folders / "pending").glob("*.json"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("answer,category", [
    ({"status": 429, "retry_after": 30}, "rate_limited"),
    ({"content": '{"answer": "o', "finish_reason": "length"}, "invalid_provider_response"),
    ({"refusal": "no"}, "content_rejected"),
])
async def test_simulated_failures_reach_agentgate_as_real_ones(folders: Path, answer: dict[str, Any], category: str,
                                                               stream: bool) -> None:
    # A streamed request is held until answered, so an error status is a real HTTP status.
    provider = _provider(ai_simulator.build_app(None))
    request = AITextRequest(system="s", prompt="p", response_format="json_object", json_schema=SCHEMA,
                            schema_name="decision", stream=stream)
    with pytest.raises(AIProviderError) as raised:
        await asyncio.gather(provider.generate_text(model="claude-sim", request=request),
                             _answer_when_asked(folders, answer))
    assert raised.value.category == category


@pytest.mark.asyncio
async def test_a_recorded_scenario_replays_without_a_person(folders: Path) -> None:
    transcript = folders / "recorded.jsonl"
    transcript.write_text(json.dumps({"request": {"schema_name": "decision"},
                                      "answer": {"content": {"answer": "replayed"}}}) + "\n", encoding="utf-8")
    provider = _provider(ai_simulator.build_app(transcript))
    request = AITextRequest(system="s", prompt="p", response_format="json_object", json_schema=SCHEMA,
                            schema_name="decision")
    response = await asyncio.wait_for(provider.generate_text(model="claude-sim", request=request), 10)
    assert json.loads(response.text) == {"answer": "replayed"}


@pytest.mark.asyncio
async def test_fault_injection_drops_or_loses_only_the_armed_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.execution.providers.native.github.provider import ExpandedGitHubProvider
    from app.execution.providers.native.http import ProviderTransportError

    sent: list[str] = []

    async def real_api(self: Any, method: str, path: str, credential: Any, payload: Any = None, **kwargs: Any) -> str:
        sent.append(f"{method} {path}")
        return "ok"

    monkeypatch.setattr(ExpandedGitHubProvider, "api", real_api)
    faults = tmp_path / "faults.json"
    monkeypatch.setattr(run_simulation, "FAULTS", faults)
    faults.write_text(json.dumps({"rules": [
        {"method": "POST", "path_suffix": "/issues", "mode": "dropped", "times": 1},
        {"method": "POST", "path_suffix": "/comments", "mode": "lost_response", "times": 1}]}), encoding="utf-8")
    run_simulation.install_fault_injection()
    provider: Any = object.__new__(ExpandedGitHubProvider)
    with pytest.raises(ProviderTransportError, match="simulated"):
        await provider.api("POST", "/repos/o/r/issues", "c")
    assert sent == []  # dropped: never sent
    assert await provider.api("POST", "/repos/o/r/issues", "c") == "ok"  # the rule is spent
    with pytest.raises(ProviderTransportError):
        await provider.api("POST", "/repos/o/r/issues/2/comments", "c")
    assert sent == ["POST /repos/o/r/issues", "POST /repos/o/r/issues/2/comments"]  # lost: it was sent
    assert await provider.api("GET", "/repos/o/r/issues?state=all", "c") == "ok"


def test_the_viewer_summarises_a_planning_request() -> None:
    tools = [{"scope": "github.repository.issue.read", "approvalRecommendation": "none",
              "inputSchema": {"properties": {"number": {"type": "integer"}}, "required": ["number"]}}]
    observed = {"actionHistory": [{"step": 1, "scope": "github.repository.issues.list", "status": "completed",
                                   "result": "Found 1 result: #2 TE."}], "recentEvidence": []}
    text = "\n\n".join([
        'JOB\n{"name": "List issues"}', "WORKER CHARTER\n{}", "AUTHORIZED TOOLS\n" + json.dumps(tools),
        "SAVED PLAN (same for every model)\n1. [done] List\nPLAN RULES\n- x",
        "RECORDED OBSERVATIONS\n" + json.dumps(observed), "ACTION BUDGET\n1 used of 20.",
        "PREVIOUS DECISION WAS INVALID\nSet planStep.", "AUTHORITATIVE_JSON_SCHEMA\n{}",
    ])
    lines = ai_simulator.summary({"number": 7, "schema_name": "audoryn_next_action",
                                  "messages": [{"role": "user", "content": text}]})
    joined = "\n".join(lines)
    for expected in ("REQUEST 0007", "List issues", "github.repository.issue.read", "1. [done] List",
                     "1. github.repository.issues.list completed | Found 1 result: #2 TE.", "1 used of 20",
                     "Set planStep."):
        assert expected in joined


def test_next_waits_for_a_request_after_the_given_number(folders: Path) -> None:
    (folders / "pending" / "0003.json").write_text(json.dumps({"number": 3}), encoding="utf-8")
    assert ai_simulator.wait_for_next(3, timeout=0) is None
    (folders / "pending" / "0004.json").write_text(json.dumps({"number": 4}), encoding="utf-8")
    assert ai_simulator.wait_for_next(3, timeout=0) == {"number": 4}

