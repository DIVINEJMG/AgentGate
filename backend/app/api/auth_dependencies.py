from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException, status

from app.infrastructure.auth.service import AuthenticatedUser, LocalAuthenticationService
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.session import session_factory


def bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    return token


async def authenticated_user(
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedUser:
    token = bearer_token(authorization)
    async with session_factory() as session:
        service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
        user = await service.current_user(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    return user
