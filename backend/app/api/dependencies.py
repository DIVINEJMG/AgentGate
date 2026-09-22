from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.domain.identity.errors import AuthenticationError
from app.domain.identity.principals import HumanPrincipal
from app.infrastructure.auth.provider import UnconfiguredIdentityProvider

_identity_provider = UnconfiguredIdentityProvider()


async def human_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> HumanPrincipal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    try:
        return await _identity_provider.authenticate(authorization.removeprefix("Bearer ").strip())
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
