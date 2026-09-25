from __future__ import annotations

import httpx

from app.bootstrap.settings import Settings, settings
from app.domain.ai.providers import ModelProvider, ModelRequest, ModelResponse


def _output_text(payload: dict[str, object]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    raw_output = payload.get("output")
    if not isinstance(raw_output, list):
        return ""
    for item in raw_output:
        if not isinstance(item, dict):
            continue
        raw_content = item.get("content")
        if not isinstance(raw_content, list):
            continue
        for part in raw_content:
            if (
                isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                text = str(part["text"]).strip()
                if text:
                    return text
    return ""


class UnconfiguredModelProvider(ModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        raise RuntimeError(
            "Managed Runtime planner model is not configured. "
            "Set MODEL_PROVIDER_API_KEY before executing autonomous Jobs."
        )


class OpenAIResponsesModelProvider(ModelProvider):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: int = 60,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = max(10, timeout_seconds)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        text_config: dict[str, object] | None = None
        if request.response_format == "json_object":
            text_config = {"format": {"type": "json_object"}}
        elif request.response_format == "json_schema":
            if request.json_schema is None:
                raise ValueError("JSON schema response format requires a schema.")
            text_config = {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.json_schema,
                    "strict": False,
                }
            }

        body: dict[str, object] = {
            "model": self._model,
            "instructions": request.system,
            "input": request.prompt,
            "max_output_tokens": max(256, request.max_output_tokens),
            "store": False,
            "reasoning": {"effort": request.reasoning_effort},
        }
        if text_config is not None:
            body["text"] = text_config

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/responses",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                raw = exc.response.json()
                if isinstance(raw, dict):
                    error = raw.get("error")
                    if isinstance(error, dict) and isinstance(error.get("message"), str):
                        detail = str(error["message"])[:400]
            except ValueError:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise RuntimeError(
                f"Managed Runtime model request failed with HTTP "
                f"{exc.response.status_code}{suffix}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Managed Runtime model request could not reach the provider.") from exc

        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Managed Runtime model provider returned an invalid response.")
        text = _output_text(payload)
        if not text:
            raise RuntimeError("Managed Runtime model provider returned no text output.")
        return ModelResponse(text=text, provider="openai", model=self._model)


def model_provider_from_settings(config: Settings = settings) -> ModelProvider:
    if config.model_provider_api_key is None:
        return UnconfiguredModelProvider()
    api_key = config.model_provider_api_key.get_secret_value().strip()
    if not api_key:
        return UnconfiguredModelProvider()
    return OpenAIResponsesModelProvider(
        api_key=api_key,
        model=config.model_provider_model,
        base_url=config.model_provider_base_url,
        timeout_seconds=config.model_provider_timeout_seconds,
    )
