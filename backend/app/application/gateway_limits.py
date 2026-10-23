"""The Audoryn Gateway's traffic limits, set in the Console (architecture plan, Phase 1e).

The Console sends the document over its entitlements channel; AgentGate keeps it in Redis under
the gateway's key prefix, where every gateway copy reads it. AgentGate only carries it: the Console
checks what an administrator enters and the gateway checks what it reads (an invalid document is
ignored there, never applied), so the policy rules live in those two places only. Newer versions
replace older ones, so the Console can resend safely.

Document: {"version": 3, "mode": "report" | null, "policies": {"<route group>": {...}, ...}}.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_DOCUMENT_BYTES = 64 * 1024
# Stores the document only when its version is newer; returns the version held afterwards.
_SET_IF_NEWER = """
local current = redis.call('GET', KEYS[1])
if current then
  local held = cjson.decode(current)['version']
  if tonumber(held) >= tonumber(ARGV[1]) then return {0, held} end
end
redis.call('SET', KEYS[1], ARGV[2])
return {1, tonumber(ARGV[1])}
"""


class TrafficLimitsDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    mode: Literal["off", "report", "enforce"] | None = None
    policies: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=20)


def key(prefix: str) -> str:
    return f"{prefix}:policies"


async def apply(client: Any, prefix: str, body: bytes) -> dict[str, Any]:
    if len(body) > MAX_DOCUMENT_BYTES:
        raise ValueError("document too large")
    document = TrafficLimitsDocument.model_validate_json(body)
    stored, held = await client.eval(_SET_IF_NEWER, 1, key(prefix), document.version,
                                     json.dumps(document.model_dump(), separators=(",", ":")))
    return {"result": "applied" if stored else "stale", "appliedVersion": int(held)}


async def applied_version(client: Any, prefix: str) -> int | None:
    raw = await client.get(key(prefix))
    return int(json.loads(raw)["version"]) if raw else None
