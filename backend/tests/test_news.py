"""The workspace app's "What's new" (plans, news and upgrade prompts plan, part 3): the Console's
announcements are checked, cached and filtered by plan and role; read state is the person's own."""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import news_routes
from app.application.console_billing import BillingUnavailable
from app.application.services import content_state, news
from tests.test_entitlements import give_plan
from tests.test_plan_prompts import db, fresh_cache, person  # noqa: F401 - fixtures

ANNOUNCEMENT = {"id": str(uuid4()), "kind": "event", "title": "Live demo Thursday", "body": "Join us.",
                "severity": "info", "publishedAt": "2026-10-17T09:00:00+00:00", "endsAt": None,
                "ctaLabel": "Register", "ctaUrl": "https://audoryn.com/demo", "dismissible": True, "major": True,
                "upgrade": False, "planCodes": [], "roles": []}


class Billing:
    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def content(self) -> dict[str, Any]:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(autouse=True)
def fresh_news() -> Iterator[None]:
    news.forget()
    yield
    news.forget()


def test_only_well_formed_announcements_with_safe_links_are_shown() -> None:
    clean = news._clean(ANNOUNCEMENT)
    assert clean is not None and clean["cta"] == {"label": "Register", "url": "https://audoryn.com/demo"}
    assert news._clean({**ANNOUNCEMENT, "ctaUrl": "/app/plans"})["cta"]["url"] == "/app/plans"  # type: ignore[index]
    for unsafe in ("javascript:alert(1)", "http://audoryn.com", "//evil.example", "/elsewhere"):
        assert news._clean({**ANNOUNCEMENT, "ctaUrl": unsafe})["cta"] is None  # type: ignore[index]
    for bad in ({"id": "nope"}, {"title": ""}, {"title": "x" * 201}, {"planCodes": "team"}, {"publishedAt": None}):
        assert news._clean({**ANNOUNCEMENT, **bad}) is None
    assert news._clean({**ANNOUNCEMENT, "severity": "loud"})["severity"] == "info"  # type: ignore[index]


def test_media_only_from_one_origin_and_published_asset_keys() -> None:
    asset = f"https://media.example/public-bucket/cms-public/assets/{'a' * 64}/{uuid4()}"
    media = {"image": asset, "imageDark": asset, "video": asset, "alt": " A cover ", "width": 1600, "height": 900}
    assert news._clean({**ANNOUNCEMENT, "media": media})["media"] == {**media, "alt": "A cover"}  # type: ignore[index]
    for bad in ({"alt": ""}, {"image": asset.replace("https", "http")}, {"video": asset + "?x=1"},
                {"imageDark": asset.replace("media.example", "other.example")}, {"image": "https://media.example/x.png"}, {"image": asset.replace("/public-bucket", "/../x")}):
        assert news._clean({**ANNOUNCEMENT, "media": {**media, **bad}})["media"] is None  # type: ignore[index]
    assert news._clean(ANNOUNCEMENT)["media"] is None  # type: ignore[index]
    video_only = {"video": asset, "alt": "A clip"}
    assert news._clean({**ANNOUNCEMENT, "media": video_only})["media"] == video_only  # type: ignore[index]
    assert news._clean({**ANNOUNCEMENT, "media": {"alt": "Nothing"}})["media"] is None  # type: ignore[index]


def test_announcements_reach_everyone_unless_limited_to_plans_or_roles() -> None:
    items = [cast(dict[str, Any], news._clean({**ANNOUNCEMENT, "id": str(uuid4()), **limits})) for limits in (
        {}, {"planCodes": ["free"]}, {"roles": ["owner", "admin"]}, {"planCodes": ["team"], "roles": ["viewer"]})]
    seen = news.for_person(items, plan_code="free", role="owner")
    assert [item["id"] for item in seen] == [items[0]["id"], items[1]["id"], items[2]["id"]]
    assert "planCodes" not in seen[0] and "roles" not in seen[0]
    assert len(news.for_person(items, plan_code="team", role="viewer")) == 2


@pytest.mark.asyncio
async def test_the_consoles_answer_is_cached_and_kept_while_it_is_away(monkeypatch: pytest.MonkeyPatch) -> None:
    billing = Billing({"announcements": [ANNOUNCEMENT, {"id": "broken"}]}, BillingUnavailable("unavailable", "x"))
    assert [item["title"] for item in await news.announcements(cast(Any, billing))] == ["Live demo Thursday"]
    assert len(await news.announcements(cast(Any, billing))) == 1 and billing.calls == 1  # cached
    monkeypatch.setattr(news, "CACHE_SECONDS", 0.0)
    assert len(await news.announcements(cast(Any, billing))) == 1 and billing.calls == 2  # last good answer
    assert await news.announcements(None) == []


@pytest.mark.asyncio
async def test_read_state_is_the_persons_own_and_only_for_news_keys(db: Any,  # noqa: F811
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(news_routes, "load_channel", lambda _settings: None)
    organization = uuid4()
    await give_plan(db, organization)
    ada, grace = await person(db, organization, admin=False), await person(db, organization, admin=True)
    key = f"news:announcement:{ANNOUNCEMENT['id']}"

    async def call(principal: Any, keys: list[str], mark: str) -> dict[str, Any]:
        async with db.session() as session:
            return await news_routes.news_state_v1(organization, news_routes.NewsState(keys=keys, mark=cast(Any, mark)),
                                                   principal, cast(Any, session))

    assert await call(ada, [key, key, "news:changelog:abc-1"], "read") == {"marked": 2}
    await call(ada, [key], "dismissed")
    with pytest.raises(HTTPException):
        await call(ada, ["prompt:x:tasks.monthly"], "read")
    async with db.session() as session:
        mine = await content_state.states(session, ada.user_id, prefix=news.PREFIX)
        theirs = await content_state.states(session, grace.user_id, prefix=news.PREFIX)
    assert set(mine) == {key, "news:changelog:abc-1"} and all(mine[key]) and theirs == {}

    async with db.session() as session:
        page = await news_routes.news_v1(organization, grace, cast(Any, session))
    assert page["announcements"] == [] and page["state"] == {} and page["canUpgrade"] is True
