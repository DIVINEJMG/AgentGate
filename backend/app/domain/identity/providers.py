from typing import Protocol

from app.domain.identity.principals import HumanPrincipal


class IdentityProvider(Protocol):
    async def authenticate(self, token: str) -> HumanPrincipal: ...
