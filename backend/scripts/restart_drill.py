"""Restart drill (architecture plan, Phase 0): proves a graceful restart lets running steps finish.

Starts the simulation backend (scripts/run_simulation.py) with RUNTIME_DRAIN_SECONDS, waits until
it is ready, waits until a log line contains `--when` (by default, the moment a step starts its
model call) or `--after` seconds pass (start a scenario with scripts/sim_replay.py meanwhile),
stops it gracefully, reports how the shutdown went, then starts it again and keeps it serving
until Ctrl+C. Needs the same stack as a simulation (ai-simulator, the local QStash).

    python scripts/restart_drill.py --drain 120
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[1]
SHUTDOWN_LINES = ("Draining", "Cancelled", "Background runtime interrupted", "Finished server process")


def start(port: int, drain: int, env: dict[str, str] | None = None) -> tuple[subprocess.Popen[str], list[str]]:
    process = subprocess.Popen(
        [sys.executable, str(BACKEND / "scripts" / "run_simulation.py"), "--port", str(port)],
        cwd=BACKEND, env={**os.environ, "RUNTIME_DRAIN_SECONDS": str(drain), **(env or {})}, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    lines: list[str] = []

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.append(line)
            sys.stdout.write(line)

    threading.Thread(target=pump, daemon=True).start()
    return process, lines


def wait_ready(port: int, process: subprocess.Popen[str], timeout: float = 300) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health/ready", timeout=30).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    process.kill()
    raise SystemExit(f"The backend on port {port} did not become ready in {timeout:.0f}s.")


def stop(process: subprocess.Popen[str], timeout: float) -> tuple[float, int | None]:
    """Sends the graceful stop a process manager sends; returns the seconds it took and the exit code."""
    began = time.monotonic()
    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)  # type: ignore[attr-defined]
    try:
        code = process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        code = None
    return time.monotonic() - began, code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--when", default="inference started", help="restart when a log line contains this")
    parser.add_argument("--after", type=float, default=600, help="restart after this many seconds at the latest")
    parser.add_argument("--drain", type=int, default=120, help="RUNTIME_DRAIN_SECONDS for the backend")
    args = parser.parse_args()

    process, lines = start(args.port, args.drain)
    wait_ready(args.port, process)
    print(f"\n[drill] ready on {args.port}; restarting at '{args.when}' or in {args.after:.0f}s. "
          "Start a scenario now.\n", flush=True)
    seen, deadline = len(lines), time.monotonic() + args.after
    while time.monotonic() < deadline and not any(args.when in line for line in lines[seen:]):
        time.sleep(0.2)
    seen = len(lines)
    seconds, code = stop(process, args.drain + 30)
    print(f"\n[drill] shutdown took {seconds:.1f}s, exit code {'killed' if code is None else code}")
    for line in lines[seen:]:
        if any(marker in line for marker in SHUTDOWN_LINES):
            print("[drill]   " + line.rstrip())

    process, _ = start(args.port, args.drain)
    wait_ready(args.port, process)
    print(f"\n[drill] serving again on {args.port}; Ctrl+C stops it.\n", flush=True)
    try:
        process.wait()
    except KeyboardInterrupt:
        stop(process, args.drain + 30)


if __name__ == "__main__":
    main()
