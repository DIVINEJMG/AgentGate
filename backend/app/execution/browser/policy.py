from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urljoin, urlsplit


class BrowserNavigationBlocked(PermissionError):
    def __init__(self, decision: BrowserNavigationDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True, slots=True)
class BrowserNavigationDecision:
    allowed: bool
    source_url: str | None
    target_url: str
    target_origin: str | None
    reason: str
    cross_origin: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_origin(value: str) -> str | None:
    raw = value.strip()
    if raw.startswith("origin:"):
        raw = raw.removeprefix("origin:")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    port = f":{parts.port}" if parts.port is not None else ""
    return f"{parts.scheme.lower()}://{parts.hostname.lower()}{port}"


def _split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class BrowserDomainPolicy:
    allowed_origins: tuple[str, ...]
    denied_origins: tuple[str, ...] = ()
    allowed_path_prefixes: tuple[str, ...] = ()
    denied_path_prefixes: tuple[str, ...] = ()
    allow_private_network: bool = False

    @classmethod
    def from_configuration(
        cls,
        configuration: dict[str, str],
        *,
        fallback_url: str | None = None,
    ) -> BrowserDomainPolicy:
        configured_allowed = _split_csv(configuration.get("allowedOrigins"))
        if not configured_allowed and fallback_url:
            configured_allowed = (fallback_url,)

        allowed: list[str] = []
        for item in configured_allowed:
            origin = normalize_origin(item)
            if origin is None:
                raise ValueError(f"Invalid allowed browser origin: {item}.")
            if origin not in allowed:
                allowed.append(origin)

        denied: list[str] = []
        for item in _split_csv(configuration.get("deniedOrigins")):
            origin = normalize_origin(item)
            if origin is None:
                raise ValueError(f"Invalid denied browser origin: {item}.")
            if origin not in denied:
                denied.append(origin)

        return cls(
            allowed_origins=tuple(allowed),
            denied_origins=tuple(denied),
            allowed_path_prefixes=_split_csv(configuration.get("allowedPaths")),
            denied_path_prefixes=_split_csv(configuration.get("deniedPaths")),
            allow_private_network=(
                configuration.get("allowPrivateNetwork", "false").strip().lower() == "true"
            ),
        )

    @property
    def primary_origin(self) -> str | None:
        return self.allowed_origins[0] if self.allowed_origins else None

    def permits(
        self, target_url: str, *, source_url: str | None = None
    ) -> BrowserNavigationDecision:
        absolute = urljoin(source_url or "", target_url)
        target = urlsplit(absolute)
        target_origin = normalize_origin(absolute)
        source_origin = normalize_origin(source_url or "") if source_url else None
        cross_origin = (
            source_origin is not None
            and target_origin is not None
            and source_origin != target_origin
        )

        if target.scheme not in {"http", "https"} or target_origin is None:
            return BrowserNavigationDecision(
                allowed=False,
                source_url=source_url,
                target_url=absolute,
                target_origin=target_origin,
                reason="Browser destination must be an absolute HTTP or HTTPS URL.",
                cross_origin=cross_origin,
            )
        if target_origin in self.denied_origins:
            return BrowserNavigationDecision(
                allowed=False,
                source_url=source_url,
                target_url=absolute,
                target_origin=target_origin,
                reason="Browser destination origin is explicitly denied.",
                cross_origin=cross_origin,
            )
        if not self.allowed_origins or target_origin not in self.allowed_origins:
            return BrowserNavigationDecision(
                allowed=False,
                source_url=source_url,
                target_url=absolute,
                target_origin=target_origin,
                reason="Browser destination origin is not authorized for this resource.",
                cross_origin=cross_origin,
            )

        path = target.path or "/"
        if self.denied_path_prefixes and any(
            path.startswith(prefix) for prefix in self.denied_path_prefixes
        ):
            return BrowserNavigationDecision(
                allowed=False,
                source_url=source_url,
                target_url=absolute,
                target_origin=target_origin,
                reason="Browser destination path is explicitly denied.",
                cross_origin=cross_origin,
            )
        if self.allowed_path_prefixes and not any(
            path.startswith(prefix) for prefix in self.allowed_path_prefixes
        ):
            return BrowserNavigationDecision(
                allowed=False,
                source_url=source_url,
                target_url=absolute,
                target_origin=target_origin,
                reason="Browser destination path is outside the authorized path scope.",
                cross_origin=cross_origin,
            )

        return BrowserNavigationDecision(
            allowed=True,
            source_url=source_url,
            target_url=absolute,
            target_origin=target_origin,
            reason=(
                "Approved cross-origin browser transition."
                if cross_origin
                else "Approved browser destination."
            ),
            cross_origin=cross_origin,
        )
