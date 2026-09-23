from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = ROOT / "frontend" / "src"
REFERENCE_ROOT = ROOT / "reference" / "typescript-backend"
BACKEND_ROOT = ROOT / "backend"

API_CALL = re.compile(
    r"""api\.(get|post|put|patch|delete)\(\s*([\`'"])(.+?)\2""",
    re.IGNORECASE,
)
REFERENCE_ROUTE = re.compile(
    r"""(?:app|router)\.(get|post|put|patch|delete)\(\s*([\`'"])(.+?)\2""",
    re.IGNORECASE,
)
REFERENCE_OBJECT_ROUTE = re.compile(
    r"""([\`'"])(GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\`'"]+)\1\s*:""",
    re.IGNORECASE,
)
QUERY_TEMPLATE = re.compile(r"\$\{qs\([^}]*\)\}")
SUFFIX_TEMPLATE = re.compile(r"\$\{suffix\}$")
TEMPLATE_EXPR = re.compile(r"\$\{[^}]+\}")
PATH_PARAM = re.compile(r"\{[^}]+\}")
COLON_PARAM = re.compile(r":[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True, order=True)
class Endpoint:
    method: str
    path: str
    source: str


def normalize_path(path: str) -> str:
    path = QUERY_TEMPLATE.sub("", path)
    path = SUFFIX_TEMPLATE.sub("", path)
    path = path.split("?", 1)[0]
    path = TEMPLATE_EXPR.sub("{param}", path)
    path = PATH_PARAM.sub("{param}", path)
    path = COLON_PARAM.sub("{param}", path)
    path = re.sub(r"/api/(?:v1|v2)(?=/|$)", "/api/{param}", path)
    path = re.sub(r"//+", "/", path)
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def frontend_endpoints() -> list[Endpoint]:
    endpoints: set[Endpoint] = set()
    files = sorted(FRONTEND_ROOT.rglob("*.ts")) + sorted(
        FRONTEND_ROOT.rglob("*.tsx")
    )
    for file in files:
        text = file.read_text(encoding="utf-8")
        for method, _, raw_path in API_CALL.findall(text):
            if raw_path.startswith("/api/"):
                endpoints.add(
                    Endpoint(
                        method.upper(),
                        normalize_path(raw_path),
                        str(file.relative_to(ROOT)),
                    )
                )
    return sorted(endpoints)


def reference_endpoints() -> list[Endpoint]:
    endpoints: set[Endpoint] = set()
    for file in sorted(REFERENCE_ROOT.rglob("*.ts")):
        text = file.read_text(encoding="utf-8")
        for method, _, raw_path in REFERENCE_ROUTE.findall(text):
            if raw_path.startswith("/api/"):
                endpoints.add(
                    Endpoint(
                        method.upper(),
                        normalize_path(raw_path),
                        str(file.relative_to(ROOT)),
                    )
                )
        for _, method, raw_path in REFERENCE_OBJECT_ROUTE.findall(text):
            endpoints.add(
                Endpoint(
                    method.upper(),
                    normalize_path(raw_path),
                    str(file.relative_to(ROOT)),
                )
            )
    return sorted(endpoints)


def python_endpoints() -> list[Endpoint]:
    sys.path.insert(0, str(BACKEND_ROOT))
    from app.bootstrap.application import create_application

    application = create_application()
    schema = application.openapi()
    endpoints: set[Endpoint] = set()
    for path, operations in schema.get("paths", {}).items():
        if not isinstance(operations, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            if method in operations:
                endpoints.add(
                    Endpoint(
                        method.upper(),
                        normalize_path(path),
                        "FastAPI OpenAPI",
                    )
                )
    return sorted(endpoints)


def signature(endpoint: Endpoint) -> tuple[str, str]:
    return endpoint.method, endpoint.path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit live Aduoryn endpoint parity.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate that frontend/reference/Python inventories are discoverable.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Fail when any active frontend contract is missing from FastAPI.",
    )
    args = parser.parse_args()

    frontend = frontend_endpoints()
    reference = reference_endpoints()
    python = python_endpoints()

    if not frontend:
        raise SystemExit("Endpoint audit found no active frontend API contracts.")
    if not reference:
        raise SystemExit("Endpoint audit found no TypeScript reference routes.")
    if not python:
        raise SystemExit("Endpoint audit found no FastAPI routes.")

    py = {signature(item) for item in python}
    ref = {signature(item) for item in reference}

    missing = [item for item in frontend if signature(item) not in py]
    present = [item for item in frontend if signature(item) in py]
    reference_missing = [
        item for item in frontend if signature(item) not in ref
    ]

    counts = Counter(
        "present" if signature(item) in py else "missing"
        for item in frontend
    )

    print("# Aduoryn Product Endpoint Parity")
    print(f"Frontend contracts: {len(frontend)}")
    print(f"FastAPI contracts: {len(py)}")
    print(f"TypeScript reference contracts: {len(ref)}")
    print(f"Present: {counts['present']}")
    print(f"Missing: {counts['missing']}")
    print(f"Frontend contracts absent from reference: {len(reference_missing)}")
    print()

    if missing:
        print("## Missing from FastAPI")
        for item in missing:
            print(
                f"- {item.method} \`{item.path}\` "
                f"({item.source})"
            )
        print()

    print("## Present in FastAPI")
    for item in present:
        print(f"- {item.method} \`{item.path}\`")

    if args.check:
        print()
        print("Endpoint inventory discovery passed.")

    if args.require_complete and missing:
        print()
        print(
            f"Endpoint parity FAILED: {len(missing)} active frontend "
            "contract(s) are not implemented by FastAPI."
        )
        return 1

    if args.require_complete:
        print()
        print("Endpoint parity PASSED: every active frontend contract is implemented.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
