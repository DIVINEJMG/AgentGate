import asyncio
import hashlib
import json
from collections.abc import Iterable

import asyncpg

from app.bootstrap.settings import settings

TABLE_ORDER = (
    "human_identities",
    "organizations",
    "organization_memberships",
    "agent_identities",
    "agent_credentials",
    "workforce_roles",
    "workers",
    "jobs",
    "job_revisions",
    "work_items",
    "runs",
    "run_steps",
    "integrations",
    "integration_credentials",
    "capability_profiles",
    "policies",
    "policy_revisions",
    "actions",
    "approvals",
    "risk_events",
    "incidents",
    "execution_controls",
    "memories",
    "artifacts",
    "results",
    "result_versions",
    "result_exports",
    "audit_events",
    "outbox_events",
    "migration_batches",
    "migration_staging_records",
    "migration_id_mappings",
)

SHADOW_TABLES = (
    "organizations",
    "workers",
    "jobs",
    "results",
    "audit_events",
)


def _asyncpg_dsn(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _quote_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError("Unsafe SQL identifier.")
    return f'"{value}"'


async def _table_exists(connection: asyncpg.Connection, table: str) -> bool:
    return bool(
        await connection.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema='public' AND table_name=$1
            )
            """,
            table,
        )
    )


async def table_count(connection: asyncpg.Connection, table: str) -> int:
    if not await _table_exists(connection, table):
        return 0
    return int(await connection.fetchval(f"SELECT count(*) FROM {_quote_identifier(table)}"))


async def migrate_table(
    source: asyncpg.Connection,
    destination: asyncpg.Connection,
    table: str,
) -> tuple[int, int]:
    if not await _table_exists(source, table):
        return 0, await table_count(destination, table)

    rows = await source.fetch(f"SELECT * FROM {_quote_identifier(table)}")
    if rows:
        columns = list(rows[0].keys())
        quoted_columns = ", ".join(_quote_identifier(column) for column in columns)
        placeholders = ", ".join("$" + str(index) for index in range(1, len(columns) + 1))
        sql = (
            f"INSERT INTO {_quote_identifier(table)} ({quoted_columns}) "
            f"VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        )
        async with destination.transaction():
            for row in rows:
                await destination.execute(sql, *(row[column] for column in columns))

    return len(rows), await table_count(destination, table)


async def migrate() -> dict[str, dict[str, int]]:
    if settings.legacy_database_url is None:
        raise RuntimeError("LEGACY_DATABASE_URL is required for provider migration.")

    source = await asyncpg.connect(
        _asyncpg_dsn(settings.legacy_database_url.get_secret_value())
    )
    destination = await asyncpg.connect(_asyncpg_dsn(settings.database_dsn))
    try:
        report: dict[str, dict[str, int]] = {}
        for table in TABLE_ORDER:
            source_count, destination_count = await migrate_table(
                source,
                destination,
                table,
            )
            report[table] = {
                "source": source_count,
                "destination": destination_count,
            }
        return report
    finally:
        await source.close()
        await destination.close()


async def _table_digest(
    connection: asyncpg.Connection,
    table: str,
) -> tuple[int, str]:
    if not await _table_exists(connection, table):
        return 0, hashlib.sha256(b"[]").hexdigest()

    rows = await connection.fetch(f"SELECT * FROM {_quote_identifier(table)}")
    normalized = [
        {key: str(value) for key, value in sorted(dict(row).items())}
        for row in rows
    ]
    normalized.sort(key=lambda row: json.dumps(row, sort_keys=True))
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return len(rows), hashlib.sha256(payload).hexdigest()


async def shadow_verify(
    tables: Iterable[str] = SHADOW_TABLES,
) -> dict[str, dict[str, object]]:
    if settings.legacy_database_url is None:
        raise RuntimeError("LEGACY_DATABASE_URL is required for shadow verification.")

    source = await asyncpg.connect(
        _asyncpg_dsn(settings.legacy_database_url.get_secret_value())
    )
    destination = await asyncpg.connect(_asyncpg_dsn(settings.database_dsn))
    try:
        result: dict[str, dict[str, object]] = {}
        for table in tables:
            source_count, source_hash = await _table_digest(source, table)
            destination_count, destination_hash = await _table_digest(destination, table)
            result[table] = {
                "sourceCount": source_count,
                "destinationCount": destination_count,
                "matched": source_count == destination_count
                and source_hash == destination_hash,
            }
        return result
    finally:
        await source.close()
        await destination.close()


async def main() -> None:
    migration = await migrate()
    print(json.dumps({"migration": migration}, indent=2))
    shadow = await shadow_verify()
    print(json.dumps({"shadow": shadow}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
