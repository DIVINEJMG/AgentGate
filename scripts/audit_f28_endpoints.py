from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = ROOT / "frontend" / "src" / "lib"
REFERENCE_ROOT = ROOT / "reference" / "typescript-backend"
BACKEND_ROOT = ROOT / "backend"

API_CALL = re.compile(
    r"""api\.(get|post|put|patch|delete)\(\s*([\`'"])"""
    r"""(.+?)\2""",
    re.IGNORECASE,
)
REFERENCE_ROUTE = re.compile(
    r"""(?:app|router)\.(get|post|put|patch|delete)\(\s*([\`'"])"""
    r"""(.+?)\2""",
    re.IGNORECASE,
)
TEMPLATE_EXPR = re.compile(r"\$\{[^}]+\}")
PATH_PARAM = re.compile(r"\{[^}]+\}")

LIVE_VERIFIED = {
    ("GET", "/api/{param}/system/status"),
    ("POST", "/api/{param}/organizations"),
    ("GET", "/api/{param}/organizations"),
    ("POST", "/api/{param}/auth/signup"),
    ("POST", "/api/{param}/auth/login"),
    ("GET", "/api/{param}/auth/session"),
    ("POST", "/api/{param}/auth/logout"),
}


@dataclass(frozen=True, order=True)
class Endpoint:
    method: str
    path: str
    source: str


def normalize_path(path: str) -> str:
    path = path.split("?", 1)[0]
    path = TEMPLATE_EXPR.sub("{param}", path)
    path = PATH_PARAM.sub("{param}", path)
    path = COLON_PARAM.sub("{param}", path)
    path = re.sub(r"/api/(?:v1|v2)/", "/api/{param}/", path)
    path = re.sub(r"//+", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def frontend_endpoints() -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for file in sorted(FRONTEND_ROOT.glob("*Api.ts")):
        text = file.read_text(encoding="utf-8")
        for method, _, raw_path in API_CALL.findall(text):
            if raw_path.startswith("/api/"):
                endpoints.append(
                    Endpoint(method.upper(), normalize_path(raw_path), str(file.relative_to(ROOT)))
                )
    return sorted(set(endpoints))


def reference_endpoints() -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for file in sorted(REFERENCE_ROOT.rglob("*.ts")):
        text = file.read_text(encoding="utf-8")
        for method, _, raw_path in REFERENCE_ROUTE.findall(text):
            if raw_path.startswith("/api/"):
                endpoints.append(
                    Endpoint(method.upper(), normalize_path(raw_path), str(file.relative_to(ROOT)))
                )
        for _, method, raw_path in REFERENCE_OBJECT_ROUTE.findall(text):
            endpoints.append(
                Endpoint(method.upper(), normalize_path(raw_path), str(file.relative_to(ROOT)))
            )
    return sorted(set(endpoints))


def python_endpoints() -> list[Endpoint]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from fastapi.routing import APIRoute
    from app.bootstrap.application import create_application

    application = create_application()
    endpoints: list[Endpoint] = []
    for route in application.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            endpoints.append(Endpoint(method, normalize_path(route.path), "FastAPI"))
    return sorted(set(endpoints))


def signature(endpoint: Endpoint) -> tuple[str, str]:
    return endpoint.method, endpoint.path


def status_for(
    endpoint: Endpoint,
    python_signatures: set[tuple[str, str]],
    reference_signatures: set[tuple[str, str]],
) -> str:
    sig = signature(endpoint)
    if sig in python_signatures:
        return "WORKING" if sig in LIVE_VERIFIED else "PARTIAL"
    if sig in reference_signatures:
        return "MISSING"
    return "MISSING"


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit F28 endpoint parity.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that all three inventories are discoverable without gating incomplete migration.",
    )
    args = parser.parse_args()

    frontend = frontend_endpoints()
    reference = reference_endpoints()
    python = python_endpoints()

    if not frontend:
        raise SystemExit("F28 audit found no frontend API endpoints.")
    if not reference:
        raise SystemExit("F28 audit found no TypeScript reference endpoints.")
    if not python:
        raise SystemExit("F28 audit found no FastAPI endpoints.")

    python_signatures = {signature(item) for item in python}
    reference_signatures = {signature(item) for item in reference}

    rows = []
    for endpoint in frontend:
        rows.append(
            (
                status_for(endpoint, python_signatures, reference_signatures),
                endpoint.method,
                endpoint.path,
                endpoint.source,
                signature(endpoint) in reference_signatures,
                signature(endpoint) in python_signatures,
            )
        )

    legacy_only = sorted(
        sig
        for sig in reference_signatures
        if sig not in {signature(item) for item in frontend}
        and sig not in python_signatures
    )
    counts = Counter(row[0] for row in rows)

    print("# F28 Endpoint Parity Audit")
    print()
    print(f"Frontend contracts: {len(rows)}")
    print(f"TypeScript reference routes: {len(reference_signatures)}")
    print(f"FastAPI routes: {len(python_signatures)}")
    print("Statuses: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    print(f"Legacy-only reference routes: {len(legacy_only)}")
    print()
    print("| Status | Method | Contract | Frontend source | TS reference | Python |")
    print("| --- | --- | --- | --- | --- | --- |")
    for status, method, path, source, in_reference, in_python in rows:
        print(
            f"| {status} | {method} | `{path}` | `{source}` | "
            f"{'yes' if in_reference else 'no'} | {'yes' if in_python else 'no'} |"
        )

    if legacy_only:
        print()
        print("## Legacy-only reference routes")
        for method, path in legacy_only:
            print(f"- {method} `{path}`")

    if args.check:
        print()
        print("F28 inventory discovery check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
