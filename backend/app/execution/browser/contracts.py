from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

BrowserSessionStatus = Literal["active", "closed", "expired", "terminated", "failed"]
BrowserLocatorStrategy = Literal["observation_ref", "role", "label", "text", "css"]


@dataclass(frozen=True, slots=True)
class BrowserSession:
    id: UUID
    organization_id: UUID
    worker_id: UUID | None
    run_id: UUID | None
    browser_context_id: str
    created_at: datetime
    expires_at: datetime
    status: BrowserSessionStatus
    current_url: str | None = None
    current_origin: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "organizationId": str(self.organization_id),
            "workerId": str(self.worker_id) if self.worker_id else None,
            "runId": str(self.run_id) if self.run_id else None,
            "browserContextId": self.browser_context_id,
            "createdAt": self.created_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "status": self.status,
            "currentUrl": self.current_url,
            "currentOrigin": self.current_origin,
        }


@dataclass(frozen=True, slots=True)
class BrowserLocator:
    strategy: BrowserLocatorStrategy
    value: str
    name: str | None = None
    observation_id: str | None = None
    exact: bool = True

    @classmethod
    def from_mapping(cls, value: object) -> BrowserLocator:
        if not isinstance(value, dict):
            raise TypeError("Browser locator must be an object.")
        strategy = str(value.get("strategy", "")).strip()
        locator_value = str(value.get("value", "")).strip()
        supported = {"observation_ref", "role", "label", "text", "css"}
        if strategy not in supported:
            raise ValueError("Unsupported browser locator strategy.")
        if not locator_value:
            raise ValueError("Browser locator value is required.")
        name_raw = value.get("name")
        observation_raw = value.get("observationId")
        return cls(
            strategy=strategy,  # type: ignore[arg-type]
            value=locator_value,
            name=str(name_raw) if name_raw is not None else None,
            observation_id=str(observation_raw) if observation_raw is not None else None,
            exact=value.get("exact") is not False,
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BrowserElement:
    ref: str
    tag: str
    role: str | None
    name: str | None
    text: str
    element_type: str | None
    value: str | None
    checked: bool | None
    selected: bool | None
    disabled: bool
    href: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BrowserFrame:
    name: str | None
    url: str
    origin: str | None
    is_main: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BrowserObservation:
    id: str
    session_id: UUID
    url: str
    title: str
    visible_text: str
    aria_snapshot: str
    dom_snapshot: str
    elements: tuple[BrowserElement, ...]
    forms: tuple[str, ...] = ()
    links: tuple[str, ...] = ()
    buttons: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    frames: tuple[BrowserFrame, ...] = ()
    page_state: dict[str, object] = field(default_factory=dict)
    observed_at: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sessionId": str(self.session_id),
            "url": self.url,
            "title": self.title,
            "visibleText": self.visible_text,
            "ariaSnapshot": self.aria_snapshot,
            "domSnapshot": self.dom_snapshot,
            "elements": [item.as_dict() for item in self.elements],
            "forms": list(self.forms),
            "links": list(self.links),
            "buttons": list(self.buttons),
            "inputs": list(self.inputs),
            "frames": [item.as_dict() for item in self.frames],
            "pageState": dict(self.page_state),
            "observedAt": self.observed_at.isoformat() if self.observed_at else None,
        }
