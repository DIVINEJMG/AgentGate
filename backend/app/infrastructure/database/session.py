from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.bootstrap.settings import settings
from app.runtime.outbox_trigger import request_outbox_drain_after_commit

engine = create_async_engine(settings.database_dsn, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@event.listens_for(Session, "after_commit")
def _request_outbox_drain_after_commit(session: Session) -> None:
    if not session.info.pop("outbox_pending", False):
        return
    request_outbox_drain_after_commit(reason="transaction_committed")


async def database_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
