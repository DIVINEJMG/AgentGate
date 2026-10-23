from typing import Protocol

from audoryn_shared.domain.identity.principals import HumanPrincipal


class IdentityProvider(Protocol):
    async def authenticate(self, token: str) -> HumanPrincipal: ...
