"""The execution service runs on its own: shared code and its own, never the core API (Phase 4)."""
from __future__ import annotations

import subprocess
import sys

SCRIPT = """
import sys
sys.modules["app"] = None  # the core API is not in the execution image
from audoryn_execution.service import create_application

def walk(route):
    nested = getattr(route, "effective_candidates", None)  # an included router
    return [path for child in nested() for path in walk(child)] if nested else [route.path]

print(sorted({path for route in create_application().routes for path in walk(route)}))
"""


def test_the_service_builds_alone_with_only_its_routes():
    result = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "/internal/v1/runtime/execute" in result.stdout and "/health/live" in result.stdout
    assert "/api/v1" not in result.stdout
