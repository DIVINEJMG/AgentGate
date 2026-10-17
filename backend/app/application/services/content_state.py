"""What one person has read or dismissed in the workspace app (`user_content_state`): upgrade
prompts and "What's new" entries. Only this person's own rows are ever read or written."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models import UserContentState

Mark = Literal["read", "dismissed"]


async def mark(session: AsyncSession, user_id: UUID, keys: list[str], how: Mark) -> None:
    """Records that the person read (or dismissed) each item now; marking again moves the time."""
    now = datetime.now(UTC)
    rows = {row.item_key: row for row in (await session.scalars(select(UserContentState).where(
        UserContentState.user_id == user_id, UserContentState.item_key.in_(keys)))).all()} if keys else {}
    for key in dict.fromkeys(keys):
        row = rows.get(key) or UserContentState(user_id=user_id, item_key=key)
        setattr(row, f"{how}_at", now)
        row.updated_at = now
        session.add(row)
    await session.flush()


async def states(session: AsyncSession, user_id: UUID, *, keys: list[str] | None = None, prefix: str | None = None,
                 limit: int = 500) -> dict[str, tuple[datetime | None, datetime | None]]:
    """(read at, dismissed at) for the person's items: the given keys, or the newest under a prefix."""
    query = select(UserContentState.item_key, UserContentState.read_at, UserContentState.dismissed_at).where(
        UserContentState.user_id == user_id)
    if keys is not None:
        if not keys:
            return {}
        query = query.where(UserContentState.item_key.in_(keys))
    if prefix is not None:
        query = query.where(UserContentState.item_key.startswith(prefix, autoescape=True))
    rows = (await session.execute(query.order_by(UserContentState.updated_at.desc()).limit(limit))).all()
    return {key: (read, dismissed) for key, read, dismissed in rows}
