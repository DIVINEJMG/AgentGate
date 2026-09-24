from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse

from app.domain.identity.errors import AuthenticationError, AuthorizationError
from app.infrastructure.auth.identity_provider import RedisSessionIdentityProvider
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.session import session_factory
from app.realtime.bus import RedisRealtimeBus

ws_router = APIRouter()
v2_router = APIRouter()


async def _authenticate(organization_id: UUID, token: str):
    if not token:
        raise AuthenticationError("Session token required.")
    async with session_factory() as session:
        provider = RedisSessionIdentityProvider(
            session,
            RedisSessionStore.from_settings(),
            organization_id=organization_id,
        )
        principal = await provider.authenticate(token)
        if principal.organization_id != organization_id:
            raise AuthorizationError("Cross-organization realtime subscription denied.")
        return principal


def _bearer_from_request(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.removeprefix("Bearer ").strip()
    return request.query_params.get("access_token", "").strip()


@ws_router.websocket("/ws/v1/organizations/{organization_id}")
async def organization_events_websocket(websocket: WebSocket, organization_id: UUID) -> None:
    token = websocket.query_params.get("access_token", "").strip()
    if not token:
        authorization = websocket.headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization.removeprefix("Bearer ").strip()
    try:
        await _authenticate(organization_id, token)
    except (AuthenticationError, AuthorizationError):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    bus = RedisRealtimeBus.from_settings()
    try:
        async for event in bus.subscribe(organization_id=organization_id):
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        await bus.close()


@v2_router.get(
    "/organizations/{organization_id}/events",
    tags=["realtime"],
)
async def organization_events_replay(
    request: Request,
    organization_id: UUID,
    cursor: str = Query(default="0-0"),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, object]:
    token = _bearer_from_request(request)
    try:
        await _authenticate(organization_id, token)
    except AuthenticationError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except AuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    bus = RedisRealtimeBus.from_settings()
    try:
        events = await bus.read(
            organization_id=organization_id,
            after=cursor,
            count=limit,
            block_ms=None,
        )
    finally:
        await bus.close()

    next_cursor = cursor
    if events:
        next_cursor = str(events[-1]["stream_id"])
    return {
        "events": events,
        "cursor": next_cursor,
    }


@v2_router.get(
    "/organizations/{organization_id}/events/stream",
    tags=["realtime"],
    response_class=StreamingResponse,
)
async def organization_events_sse(
    request: Request,
    organization_id: UUID,
    cursor: str = Query(default="0-0"),
) -> StreamingResponse:
    token = _bearer_from_request(request)
    try:
        await _authenticate(organization_id, token)
    except AuthenticationError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except AuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    async def events() -> AsyncIterator[str]:
        bus = RedisRealtimeBus.from_settings()
        current = cursor
        try:
            while not await request.is_disconnected():
                batch = await bus.read(
                    organization_id=organization_id,
                    after=current,
                    count=100,
                    block_ms=15000,
                )
                if not batch:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(0)
                    continue
                for event in batch:
                    current = str(event["stream_id"])
                    data = json.dumps(event, separators=(",", ":"), default=str)
                    yield (
                        f"id: {current}\n"
                        f"event: {event.get('event_type', 'message')}\n"
                        f"data: {data}\n\n"
                    )
        finally:
            await bus.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
