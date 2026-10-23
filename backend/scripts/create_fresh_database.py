"""Build a brand-new, empty AgentGate database (the one in DATABASE_URL).

Running every migration in order cannot build a new database: migration 0001 creates all tables
from the current models, and later migrations create some of them again. This script does what
0001 does (create every table from the models, which now also declare the indexes that later
migrations added) and then records the database as fully migrated, so future migrations apply
normally.

It refuses to run on a database that already has tables, so it can never touch an existing one.

    python scripts/create_fresh_database.py            # shows what it would do
    python scripts/create_fresh_database.py --apply    # builds the schema and stamps head

Connection details are never printed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from audoryn_shared.bootstrap.settings import settings
from audoryn_shared.infrastructure.database import models  # noqa: F401
from audoryn_shared.infrastructure.database.base import Base


async def existing_tables() -> list[str]:
    engine = create_async_engine(settings.database_dsn)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    finally:
        await engine.dispose()


async def create_schema() -> None:
    engine = create_async_engine(settings.database_dsn)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, checkfirst=False))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a brand-new, empty AgentGate database (the one in DATABASE_URL).")
    parser.add_argument("--apply", action="store_true", help="build the schema and stamp head")
    args = parser.parse_args()

    tables = asyncio.run(existing_tables())
    if tables:
        print(f"Refused: the database already has {len(tables)} tables. This script only builds empty databases.")
        return 1
    planned = len(Base.metadata.tables)
    if not args.apply:
        print(f"The database is empty. --apply would create {planned} tables and stamp the latest migration.")
        return 0
    asyncio.run(create_schema())
    command.stamp(Config(str(BACKEND / "alembic.ini")), "head")
    built = asyncio.run(existing_tables())
    print(f"Created {len([name for name in built if name != 'alembic_version'])} tables and stamped the latest migration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
