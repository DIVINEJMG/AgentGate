from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.execution.browser.policy import normalize_origin

_CLOUD_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.google",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}


@dataclass(frozen=True, slots=True)
class BrowserEgressDecision:
    allowed: bool
    host: str | None
    reason: str


def _ip_is_private_or_special(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(
        (
            address.is_private,
            address.is_loopback,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


async def evaluate_browser_egress(
    url: str,
    *,
    allow_private_network: bool = False,
) -> BrowserEgressDecision:
    parts = urlsplit(url)
    host = (parts.hostname or "").strip().lower()
    if not host:
        return BrowserEgressDecision(False, None, "Browser destination host is missing.")
    if allow_private_network:
        return BrowserEgressDecision(True, host, "Private-network egress explicitly enabled.")

    if host == "localhost" or host.endswith(".localhost"):
        return BrowserEgressDecision(False, host, "Browser egress to localhost is blocked.")
    if host in _CLOUD_METADATA_HOSTS:
        return BrowserEgressDecision(False, host, "Browser egress to cloud metadata is blocked.")
    if _ip_is_private_or_special(host):
        return BrowserEgressDecision(
            False, host, "Browser egress to private/internal IP space is blocked."
        )

    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                parts.port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            ),
            timeout=5.0,
        )
    except TimeoutError:
        return BrowserEgressDecision(
            False,
            host,
            "Browser DNS safety preflight timed out.",
        )
    except socket.gaierror:
        return BrowserEgressDecision(False, host, "Destination DNS could not be resolved safely.")

    addresses = {str(record[4][0]) for record in records if record and record[4]}
    if any(_ip_is_private_or_special(address) for address in addresses):
        return BrowserEgressDecision(
            False,
            host,
            "Browser destination resolved to private/internal IP space.",
        )
    return BrowserEgressDecision(True, host, "Browser egress destination is public.")


# Hosts remembered per browser session, so each is looked up once (a few KB at most).
MAX_REMEMBERED_HOSTS = 512


async def resource_refusal(
    url: str,
    *,
    denied_origins: tuple[str, ...],
    allow_private_network: bool,
    remembered: dict[str, BrowserEgressDecision],
) -> str | None:
    """Why a page's own request (a script, style or fetch) is refused, or None (F39).

    Blocked sites and private addresses are refused; other sites a page loads from, such as a
    CDN, stay allowed. Non-web addresses (data:, blob:) never leave the browser."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return None
    if normalize_origin(url) in denied_origins:
        return "Browser destination origin is explicitly denied."
    key = f"{parts.hostname}:{parts.port or ''}"
    decision = remembered.get(key)
    if decision is None:
        decision = await evaluate_browser_egress(url, allow_private_network=allow_private_network)
        if len(remembered) < MAX_REMEMBERED_HOSTS:
            remembered[key] = decision
    return None if decision.allowed else decision.reason
