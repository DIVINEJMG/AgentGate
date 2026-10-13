"""Improvement plan, phase 3c: when an integration task cannot be prepared, the person gets one
clear question or explanation in their own terms, with what they can choose or fix."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.services.integration_foundation import (
    IntegrationFoundation,
    IntegrationRequirementError,
    describe_capabilities,
)
from app.domain.integrations.foundation import IntegrationNeed, IntegrationTaskDraft

READ = "github.repository.metadata.read"


def repo(name: str, account: str = "acme", scopes: tuple[str, ...] = (READ,)) -> dict[str, Any]:
    return {"id": str(uuid4()), "connectionId": str(uuid4()), "provider": "github", "externalId": name,
            "name": name, "account": account, "capabilities": list(scopes), "health": "healthy",
            "webUrl": "", "aliases": []}


async def prepare(catalog: list[dict[str, Any]], *, hint: str = "", provider: str = "github") -> str:
    foundation = IntegrationFoundation(SimpleNamespace())  # type: ignore[arg-type]
    foundation.catalog = AsyncMock(return_value=catalog)  # type: ignore[method-assign]
    foundation.runtime_catalog = dict  # type: ignore[method-assign]
    draft = IntegrationTaskDraft(objective="Report on the repository", completion_criteria=["Reported"],
                                 needs=[IntegrationNeed(provider=provider, resource_hint=hint, scopes=[READ])])
    with pytest.raises(IntegrationRequirementError) as caught:
        await foundation.resolve(organization_id=uuid4(), user_id=uuid4(), draft=draft)
    return str(caught.value)


def test_capabilities_are_described_in_peoples_words() -> None:
    assert describe_capabilities("github", [READ]) == "read repository identity, default branch and access state"
    assert describe_capabilities("unknown", ["x.y"]) == "x.y"


@pytest.mark.asyncio
async def test_an_unclear_choice_lists_the_options_in_one_question() -> None:
    message = await prepare([repo("acme/api"), repo("acme/web", account="acme-bot")])
    assert message.startswith("Which GitHub repository should I use for this?")
    assert "Options: acme/api (acme), acme/web (acme-bot)." in message and message.endswith("Reply with its name.")


@pytest.mark.asyncio
async def test_a_long_choice_is_shortened() -> None:
    message = await prepare([repo(f"acme/r{index}") for index in range(11)])
    assert "acme/r7 (acme), and 3 more." in message and "acme/r8" not in message


@pytest.mark.asyncio
async def test_a_missing_permission_says_what_and_how_to_fix_it() -> None:
    message = await prepare([repo("acme/api", scopes=("github.repository.readme.read",))], hint="acme/api")
    assert message.startswith("acme/api can't do what this task needs yet: read repository identity")
    assert "Reconnect acme with that permission in Integrations" in message
    assert message.endswith(f"(Missing: {READ}.)")


@pytest.mark.asyncio
async def test_when_no_connection_can_do_it_the_message_says_so() -> None:
    message = await prepare([repo("acme/api", scopes=("github.repository.readme.read",)),
                             repo("acme/web", scopes=("github.repository.readme.read",))])
    assert message.startswith("None of your GitHub connections can do what this task needs yet")


@pytest.mark.asyncio
async def test_with_no_connection_the_person_is_asked_to_connect_one() -> None:
    assert await prepare([]) == "Connect or share the GitHub account this task should use, then reply here to continue."
