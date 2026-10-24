"""Checks the AgentGate services on this VM and prints PASS or FAIL for each check; never a secret.
Needs only the host's python3, podman and ss. Exit status 1 when any check fails.

    python3 /opt/audoryn/AgentGate/infrastructure/oracle/check_services.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request

PUBLIC = "https://130.61.104.247/health/ready"
# Each container: its service, its port, the role it must run, and what its settings must say. The core
# copies are instances of one template, named by their port (quadlet/audoryn-api@.container).
CONTAINERS = {
    "audoryn-gateway": {"unit": "audoryn-gateway", "port": 8000},
    **{f"audoryn-api-{port}": {"unit": f"audoryn-api@{port}", "port": port, "role": "api",
                               "call_url": "http://127.0.0.1:8000", "cors": False} for port in (8101, 8102)},
    "audoryn-execution": {"unit": "audoryn-execution", "port": 8201, "role": "execution", "drain": 120},
}
# How many copies each of the gateway's pools must have in rotation, and the gateway's limits mode.
POOLS = {"core": 2, "execution": 1}
LIMITS_MODE = "enforce"
SETTINGS = ("import json; from audoryn_shared.bootstrap.settings import settings as s; print(json.dumps({"
            "'role': s.service_role, 'call_url': s.execution_call_url, 'key': s.internal_call_key_secret is not None, "
            "'drain': s.runtime_drain_seconds, 'cors': s.cors_enabled}))")
failures = 0


def report(ok: bool, name: str, detail: object = "") -> None:
    global failures
    failures += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail != "" else ""))


def run(*command: str) -> str:
    return subprocess.run(command, check=False, capture_output=True, text=True).stdout.strip()


def fetch(url: str) -> tuple[int | None, object]:
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except Exception as error:  # noqa: BLE001 - every failure is reported, not raised
        return None, type(error).__name__


def main() -> None:
    listening = run("ss", "-ltn")
    for name, want in CONTAINERS.items():
        report(run("systemctl", "--user", "is-active", f"{want['unit']}.service") == "active", f"{want['unit']} active")
        image = run("podman", "inspect", "--type", "container", name, "--format", "{{.ImageName}}")
        running = run("podman", "inspect", "--type", "container", name, "--format", "{{.Image}}")
        report(bool(image) and running == run("podman", "image", "inspect", image, "--format", "{{.Id}}"),
               f"{name} runs the newest {image or 'image'}")
        report(f"127.0.0.1:{want['port']} " in listening, f"{name} listens on 127.0.0.1:{want['port']}")
        traceback = run("sh", "-c", f"podman logs --since 10m {name} 2>&1 | grep -c Traceback") or "0"
        report(traceback == "0", f"{name} has no Traceback in 10 min", traceback)
        if "role" in want:
            raw = run("podman", "exec", name, "python", "-c", SETTINGS)
            settings = json.loads(raw) if raw.startswith("{") else {}
            report(settings.get("role") == want["role"], f"{name} role is {want['role']}", settings.get("role"))
            report(settings.get("key") is True, f"{name} has the internal call key")
            if "call_url" in want:
                report(settings.get("call_url") == want["call_url"], f"{name} sends due steps to the gateway",
                       settings.get("call_url"))
            if "cors" in want:
                report(settings.get("cors") is want["cors"], "the gateway answers CORS, not " + name,
                       settings.get("cors"))
            if "drain" in want:
                report(settings.get("drain") == want["drain"], f"{name} drains {want['drain']} s", settings.get("drain"))
            status, _ = fetch(f"http://127.0.0.1:{want['port']}/health/ready")
            report(status == 200, f"{name} ready", status)
    status, health = fetch("http://127.0.0.1:8000/health/gateway")
    pools = health.get("pools", {}) if isinstance(health, dict) else {}
    for pool, copies in POOLS.items():
        rotation = pools.get(pool, {}).get("inRotation", 0)
        report(rotation == copies, f"gateway {pool} pool has {copies} in rotation", rotation)
    mode = health.get("limits", {}).get("mode") if isinstance(health, dict) else None
    report(mode == LIMITS_MODE, f"gateway limits mode is {LIMITS_MODE}", mode)
    status, _ = fetch(PUBLIC)
    report(status == 200, "ready over HTTPS through nginx and the gateway", status)
    print(f"\n{'ALL PASS' if not failures else f'{failures} FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
