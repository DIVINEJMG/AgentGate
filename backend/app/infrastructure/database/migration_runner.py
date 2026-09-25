from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _upgrade_to_head() -> None:
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")


async def upgrade_database_schema() -> None:
    """Run Alembic outside the application event loop.

    This path is deliberately feature-flagged and fail-closed: if an enabled
    migration fails, application startup fails and Render keeps the previous
    healthy release serving traffic.
    """

    await asyncio.to_thread(_upgrade_to_head)
