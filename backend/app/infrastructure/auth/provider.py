from app.domain.identity.errors import AuthenticationError
from app.domain.identity.principals import HumanPrincipal
from app.domain.identity.providers import IdentityProvider


class UnconfiguredIdentityProvider(IdentityProvider):
    """Fail-closed provider until an OIDC/SSO implementation is configured."""

    async def authenticate(self, token: str) -> HumanPrincipal:
        raise AuthenticationError("Human identity provider is not configured.")
