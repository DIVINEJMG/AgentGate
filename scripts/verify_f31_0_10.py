from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors: list[str] = []


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


adr = read("docs/adr/0025-provider-neutral-ai-gateway.md")
contracts = read("backend/app/domain/ai/providers.py")
registry = read("backend/app/domain/ai/registry.py")
gateway = read("backend/app/application/services/ai_gateway.py")
provider_factory = read("backend/app/infrastructure/ai/provider.py")
nvidia = read("backend/app/infrastructure/ai/nvidia.py")
telemetry = read("backend/app/infrastructure/ai/telemetry.py")
models = read("backend/app/infrastructure/database/models.py")
managed = read("backend/app/runtime/managed.py")
runtime_api = read("backend/app/api/internal/runtime.py")
ai_api = read("backend/app/api/internal/ai.py")
readiness = read("backend/app/application/services/readiness.py")
settings = read("backend/app/bootstrap/settings.py")
render = read("infrastructure/render/render.yaml")
env = read(".env.example")
migration = read("backend/migrations/versions/0006_f31_ai_invocations.py")
tests = read("backend/tests/test_f31_0_10.py")

for token in (
    "AI Gateway",
    "F29",
    "F30",
    "side effects",
    "AI_ENABLED",
):
    if token not in adr:
        errors.append(f"F31 ADR is missing architecture term: {token}")

for token in (
    "class AIGateway(Protocol)",
    "generate_text",
    "generate_structured",
    "analyze_media",
    "class AIProviderError",
    "class ModelProviderCapabilities",
):
    if token not in contracts:
        errors.append(f"provider-neutral AI contract is missing: {token}")

for role in ("conversation", "intent", "planner", "vision"):
    if f'role="{role}"' not in provider_factory:
        errors.append(f"model registry factory is missing role: {role}")

for token in (
    "class NvidiaNimProvider",
    "/chat/completions",
    '"image_url"',
    "stream_options",
    "X-Correlation-ID",
):
    if token not in nvidia:
        errors.append(f"NVIDIA NIM adapter is missing: {token}")

if "Draft202012Validator" not in gateway or "for repair_attempt in range(2)" not in gateway:
    errors.append("structured output adapter lacks schema validation + one repair attempt")

if "ai_gateway_from_settings(session=session)" not in managed:
    errors.append("Managed Runtime is not wired through the provider-neutral AI Gateway")
for forbidden in ("model_provider_from_settings", "OpenAI", "NvidiaNimProvider"):
    if forbidden in managed:
        errors.append(f"Managed Runtime contains vendor-specific dependency: {forbidden}")

for token in (
    "except AIProviderError",
    '"waiting_configuration"',
    '"waiting_ai"',
    "aiRetryCount",
):
    if token not in runtime_api:
        errors.append(f"runtime AI failure semantics are missing: {token}")

for token in (
    "class AIInvocation",
    "ix_ai_invocations_org_created",
):
    if token not in models:
        errors.append(f"AI invocation persistence is missing: {token}")
for forbidden in ("prompt:", "api_key:", "secret:"):
    if forbidden in telemetry:
        errors.append(f"AI telemetry recorder contains forbidden secret/prompt field: {forbidden}")

if "0006_f31_ai_invocations" not in migration:
    errors.append("AI invocation migration is missing")

for token in (
    'ai_provider: str = "nvidia_nim"',
    'ai_provider_base_url: str = "https://integrate.api.nvidia.com/v1"',
    'ai_coordinator_model: str = "nvidia/nemotron-3-ultra-550b-a55b"',
    'ai_vision_model: str = "nvidia/ising-calibration-1.5-31b"',
):
    if token not in settings:
        errors.append(f"secure AI setting is missing: {token}")

for token in (
    "AI_ENABLED=false",
    "AI_PROVIDER=nvidia_nim",
    "AI_PROVIDER_API_KEY=",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/ising-calibration-1.5-31b",
):
    if token not in env:
        errors.append(f".env.example is missing: {token}")

for token in (
    "AI_ENABLED",
    "AI_PROVIDER",
    "AI_PROVIDER_BASE_URL",
    "AI_PROVIDER_API_KEY",
    "AI_COORDINATOR_MODEL",
    "AI_VISION_MODEL",
):
    if token not in render:
        errors.append(f"Render AI deployment boundary is missing: {token}")
if "MODEL_PROVIDER_" in render:
    errors.append("legacy OpenAI-era MODEL_PROVIDER deployment keys remain in Render blueprint")

if 'value: "false"' not in render:
    errors.append("AI must remain disabled by default until an operator supplies credentials")

for token in ("QStashSignatureVerifier", '@router.post("/probe")', "AIProviderError"):
    if token not in ai_api:
        errors.append(f"protected provider probe is missing: {token}")

for token in ('"ai": self.ai', '"configured": ai_configured', '"vision": ai_state'):
    if token not in readiness:
        errors.append(f"AI readiness contract is missing: {token}")

for token in (
    "test_nvidia_text_completion_contract",
    "test_nvidia_image_completion_contract",
    "test_structured_gateway_repairs_invalid_schema_once",
    "test_ai_invocation_model_persists_metadata_not_prompt_secrets",
):
    if token not in tests:
        errors.append(f"F31 acceptance test is missing: {token}")

if errors:
    for error in errors:
        print(f"F31.0-F31.10 verification failure: {error}", file=sys.stderr)
    raise SystemExit(1)

print("F31.0-F31.10 provider-neutral AI foundation verified.")
