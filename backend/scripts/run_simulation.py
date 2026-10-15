"""Run the backend for live testing with a simulated model (scripts/ai_simulator.py).

Only the model is replaced: every chat, preparation and planning request goes to the local
simulator. Everything else is live: GitHub, E2B, Redis, the database and the queue.

Settings come from `.env`, overridden by `.env.sim` (git-ignored), then by the simulator
settings below. `.env.sim` must name its own DATABASE_URL (a Neon branch), so a simulation can
never write to the database a deployed backend uses. Point QStash at the local dev server there
(`npx @upstash/qstash-cli dev`) to spend no QStash quota.

    python scripts/run_simulation.py [--port 8000] [--simulator-port 8788]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def simulation_environment(overrides: dict[str, str], simulator_port: int) -> dict[str, str]:
    if not overrides.get("DATABASE_URL"):
        raise SystemExit(".env.sim must set DATABASE_URL to a database used only for simulation.")
    route = json.dumps(["sim:claude-sim"])
    return {
        **overrides,
        "AI_TEXT_CONNECTIONS": json.dumps({"sim": {
            "api_format": "openai_chat", "simulator": True,
            "base_url": f"http://127.0.0.1:{simulator_port}/v1",
            "hosting_provider": "simulator", "credential_ref": "simulator",
            "schema_mode": "json_schema", "enabled": True, "data_allowed": True,
        }}),
        "AI_TEXT_API_KEYS": json.dumps({"simulator": "local-simulator"}),
        "AI_INTERACTIVE_ROUTES": route,
        "AI_COMPLEX_ROUTES": route,
        "SMART_PLANNER_ROUTES": route,
        "SMART_PLANNER_MODEL_PROFILES": json.dumps({"sim:claude-sim": {
            "context_tokens": 400000, "generation_tokens": 8000,
            "output_format": "json_schema", "streaming": True,
        }}),
        # A person answers, so allow time; AgentGate's own deadlines still apply above these.
        "AI_INTERACTIVE_ATTEMPT_SECONDS": "1500",
        "AI_INTERACTIVE_TIMEOUT_SECONDS": "1800",
        "RUNTIME_PLANNER_TIMEOUT_SECONDS": "2400",
        # Usage logs list which code issued each unit's database statements.
        "AUDORYN_TRACE_STATEMENTS": "1",
    }


FAULTS = BACKEND / ".sim" / "faults.json"


def install_fault_injection() -> None:
    """Simulation only: make chosen GitHub calls fail the way a network does, so uncertain-write
    handling can be tested live. Rules live in `.sim/faults.json` and are read on every call:

        {"rules": [{"method": "POST", "path_suffix": "/issues", "mode": "dropped", "times": 1}]}

    `dropped`: the request never reaches GitHub and the call times out.
    `lost_response`: GitHub performs the request, then the response is lost (a timeout).
    Each firing lowers `times`; a rule at 0 is spent."""
    from app.execution.providers.native.github.provider import ExpandedGitHubProvider
    from app.execution.providers.native.http import ProviderTransportError

    real_api = ExpandedGitHubProvider.api

    def matching_rule(method: str, path: str) -> str | None:
        if not FAULTS.exists():
            return None
        data = json.loads(FAULTS.read_text(encoding="utf-8"))
        for rule in data.get("rules", []):
            if (rule.get("times", 0) > 0 and rule.get("method", "").upper() == method.upper()
                    and path.split("?")[0].endswith(rule.get("path_suffix", ""))):
                rule["times"] -= 1
                FAULTS.write_text(json.dumps(data, indent=1), encoding="utf-8")
                return str(rule.get("mode"))
        return None

    async def api(self, method, path, credential, payload=None, **kwargs):
        mode = matching_rule(method, path)
        if mode == "dropped":
            print(f"[faults] dropped {method} {path}", flush=True)
        elif mode == "lost_response":
            await real_api(self, method, path, credential, payload, **kwargs)
            print(f"[faults] lost the response of {method} {path}", flush=True)
        else:
            return await real_api(self, method, path, credential, payload, **kwargs)
        raise ProviderTransportError(code="timeout", retryable=True,
                                     safe_message="GitHub did not respond in time (simulated).")

    ExpandedGitHubProvider.api = api  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--simulator-port", type=int, default=8788)
    args = parser.parse_args()
    os.chdir(BACKEND)
    overrides = _read_env_file(BACKEND / ".env.sim") if (BACKEND / ".env.sim").exists() else {}
    os.environ.update(simulation_environment(overrides, args.simulator_port))
    install_fault_injection()

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
