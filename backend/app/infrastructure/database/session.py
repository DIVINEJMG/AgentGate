from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.bootstrap.settings import settings

engine = create_async_engine(settings.database_dsn, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def database_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
