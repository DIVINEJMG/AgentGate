from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SecretReference:
    id: str
    purpose: str


class SecretVault(Protocol):
    async def read(self, secret_id: str) -> str: ...


def require_secret_reference(value: str) -> SecretReference:
    if not value.startswith("secret://"):
        raise ValueError("Provider credentials must be stored as secret references.")
    secret_id = value.removeprefix("secret://").strip()
    if not secret_id:
        raise ValueError("Secret reference is empty.")
    return SecretReference(id=secret_id, purpose="provider-credential")
