from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from jsonschema import ValidationError, validate
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.services.integration_foundation import IntegrationFoundation
from app.bootstrap.settings import settings
from app.domain.ai.providers import AIGateway, AIProviderError
from app.execution.providers.native.github.provider import ExpandedGitHubProvider
from app.execution.providers.native.github.safety import validate_changes
from app.execution.providers.registry import ProviderRegistry


@pytest.mark.asyncio
async def test_task_schema_binds_provider_to_available_scopes_and_constraint_names(monkeypatch):
    monkeypatch.setattr(settings, "coding_execution_enabled", False)
    registry = ProviderRegistry((ExpandedGitHubProvider(),))
    monkeypatch.setattr("app.application.services.integration_foundation.execution_provider_registry", lambda: registry)
    service = IntegrationFoundation(cast(AsyncSession, SimpleNamespace()))
    monkeypatch.setattr(service, "catalog", AsyncMock(return_value=[]))
    gateway = SimpleNamespace(generate_structured=AsyncMock(return_value={
        "objective": "Read repository", "completion_criteria": ["Report"],
        "needs": [{"provider": "github", "scopes": ["github.repository.metadata.read"]}]}))
    await service.interpret(cast(AIGateway, gateway), organization_id=uuid4(), user_id=uuid4(), instruction="Read repository")
    schema = gateway.generate_structured.call_args.kwargs["schema"]
    good = {"objective": "Read repository", "completion_criteria": ["Report"],
        "runtime_requirements": [],
        "needs": [{"provider": "github", "scopes": ["github.repository.metadata.read"],
            "constraints": {"allowedPaths": ["backend/scripts/check_timezone_data.py"]}}]}
    validate(good, schema)
    for invalid in (
        {"provider": "e2b", "scopes": ["github.repository.metadata.read"]},
        {"provider": "github", "scopes": ["github.repository.workspace.open"]},
        {"provider": "github", "scopes": ["github.repository.metadata.read"], "constraints": {"allowed_paths": ["backend/*"]}},
    ):
        with pytest.raises(ValidationError):
            validate({**good, "needs": [invalid]}, schema)


@pytest.mark.asyncio
async def test_invented_tool_is_an_invalid_ai_response_not_missing_connection(monkeypatch):
    service = IntegrationFoundation(cast(AsyncSession, SimpleNamespace()))
    monkeypatch.setattr(service, "catalog", AsyncMock(return_value=[]))
    gateway = SimpleNamespace(generate_structured=AsyncMock(return_value={
        "objective": "Edit code", "completion_criteria": ["Report"],
        "needs": [{"provider": "e2b", "scopes": ["e2b.code.edit"]}]}))
    with pytest.raises(AIProviderError) as error:
        await service.interpret(cast(AIGateway, gateway), organization_id=uuid4(), user_id=uuid4(), instruction="Edit code")
    assert error.value.category == "invalid_provider_response"


def test_legacy_misspelled_path_constraint_cannot_authorize_publication():
    changes = [{"path": "outside.py", "content": "print('hello')"}]
    with pytest.raises(ValidationError):
        validate_changes(changes, {"allowed_paths": ["inside.py"]})
    with pytest.raises(ValueError, match="outside the task's path authority"):
        validate_changes(changes, {"allowedPaths": ["inside.py"]})
