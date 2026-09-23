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
    r"""api\.(get|post|put|patch|delete)\(\s*([\`'"])"""
    r"""(.+?)\2""",
    re.IGNORECASE,
)
REFERENCE_ROUTE = re.compile(
    r"""(?:app|router)\.(get|post|put|patch|delete)\(\s*([\`'"])"""
    r"""(.+?)\2""",
    re.IGNORECASE,
)
REFERENCE_OBJECT_ROUTE = re.compile(
    r"""([\`'"])(GET|POST|PUT|PATCH|DELETE)\s+(/api/[^\`'"]+)\1\s*:""",
    re.IGNORECASE,
)
TEMPLATE_EXPR = re.compile(r"\$\{[^}]+\}")
PATH_PARAM = re.compile(r"\{[^}]+\}")
COLON_PARAM = re.compile(r":[A-Za-z_][A-Za-z0-9_]*")

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
    from app.bootstrap.application import create_application

    application = create_application()
    schema = application.openapi()
    endpoints: list[Endpoint] = []
    for path, operations in schema.get("paths", {}).items():
        if not isinstance(operations, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            if method not in operations:
                continue
            endpoints.append(
                Endpoint(method.upper(), normalize_path(path), "FastAPI OpenAPI")
            )
    return sorted(set(endpoints))

