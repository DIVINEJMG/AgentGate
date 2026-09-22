from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import bearer_token
from app.infrastructure.auth.service import (
    AuthenticationFailure,
    LocalAuthenticationService,
    RegistrationFailure,
)
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.session import database_session

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class SignupRequest(LoginRequest):
    name: str | None = Field(default=None, max_length=160)


def _payload(user, token: str) -> dict[str, object]:
    return {
        "user": {
            "id": str(user.id),
            "userId": str(user.id),
            "email": user.email,
            "name": user.name,
        },
        "accessToken": token,
        "tokenType": "Bearer",
    }


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
    try:
        user, token = await service.register(
            email=payload.email,
            password=payload.password,
            name=payload.name,
        )
    except RegistrationFailure as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _payload(user, token)


@router.post("/login")
async def login(
    payload: LoginRequest,
    session: Annotated[AsyncSession, Depends(database_session)],
) -> dict[str, object]:
    service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
    try:
        user, token = await service.login(email=payload.email, password=payload.password)
    except AuthenticationFailure as exc:
        code = (
            status.HTTP_429_TOO_MANY_REQUESTS
            if str(exc).startswith("Too many")
            else status.HTTP_401_UNAUTHORIZED
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return _payload(user, token)


@router.get("/session")
async def session_info(
    session: Annotated[AsyncSession, Depends(database_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, object]:
    token = bearer_token(authorization)
    service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
    user = await service.current_user(token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session.")
    return _payload(user, token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    session: Annotated[AsyncSession, Depends(database_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    token = bearer_token(authorization)
    service = LocalAuthenticationService(session, RedisSessionStore.from_settings())
    await service.logout(token)
