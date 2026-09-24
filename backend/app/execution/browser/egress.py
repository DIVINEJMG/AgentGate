from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

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
        return BrowserEgressDecision(False, host, "Browser egress to private/internal IP space is blocked.")

    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            parts.port or (443 if parts.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror:
        # DNS failure is handled by the browser as a normal navigation/provider error.
        return BrowserEgressDecision(True, host, "Destination DNS could not be resolved during preflight.")

    addresses = {str(record[4][0]) for record in records if record and record[4]}
    if any(_ip_is_private_or_special(address) for address in addresses):
        return BrowserEgressDecision(
            False,
            host,
            "Browser destination resolved to private/internal IP space.",
        )
    return BrowserEgressDecision(True, host, "Browser egress destination is public.")
