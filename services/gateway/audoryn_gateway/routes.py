"""The route table: which pool serves each path. It is the boundary between the services
(docs/plans/architecture-scale-plan.md, section 3), so the backend's test suite checks that every
route the core serves is listed here and that each lands in the right pool.

The execution pool serves QStash's callbacks for background work. Until the execution service
runs on its own (Phase 3), it holds the same copies as the core pool.
"""
from __future__ import annotations

from typing import Literal

Pool = Literal["core", "execution"]

# Longest prefix first; a prefix ending in "/" covers everything below it.
ROUTES: tuple[tuple[str, Pool], ...] = (
    ("/internal/v1/runtime/", "execution"),
    ("/internal/v1/integrations/", "execution"),
    ("/internal/v1/web-research/", "execution"),
    ("/internal/v1/outbox/", "execution"),
    ("/internal/console/", "core"),
    ("/internal/v1/migration/", "core"),
    ("/internal/v1/ai/", "core"),
    ("/api/v1/", "core"),
    ("/api/v2/", "core"),
    ("/ws/v1/", "core"),
    ("/health/", "core"),
    ("/integration-events/", "core"),
    ("/integrations/github/callback", "core"),
    ("/openapi.json", "core"),  # the core serves these two outside production only
    ("/docs", "core"),
)


def pool_for(path: str) -> Pool | None:
    """The pool for this path, or None when no service serves it."""
    for prefix, pool in ROUTES:
        if path.startswith(prefix) if prefix.endswith("/") else path == prefix or path.startswith(prefix + "/"):
            return pool
    return None
