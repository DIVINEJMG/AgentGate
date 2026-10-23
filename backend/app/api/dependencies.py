from typing import Annotated

from fastapi import Header, HTTPException, status

from app.infrastructure.auth.provider import SessionIdentityProvider
from audoryn_shared.domain.identity.errors import AuthenticationError
from audoryn_shared.domain.identity.principals import HumanPrincipal

_identity_provider = SessionIdentityProvider()


async def human_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> HumanPrincipal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    try:
        return await _identity_provider.authenticate(authorization.removeprefix("Bearer ").strip())
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
