"""A database built from the models (scripts/create_fresh_database.py) has every index and
constraint the migrations create by name, so it matches a database built by migration."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from audoryn_shared.infrastructure.database import models  # noqa: F401
from audoryn_shared.infrastructure.database.base import Base

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _model_ddl() -> str:
    dialect = postgresql.dialect()
    parts = []
    for table in Base.metadata.sorted_tables:
        parts.append(str(CreateTable(table).compile(dialect=dialect)))
        parts.extend(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes)
    return "\n".join(parts)


def _named_in_migrations() -> dict[str, str]:
    """Index and constraint names that migrations after 0001 create, by migration file."""
    found: dict[str, str] = {}
    for path in sorted(MIGRATIONS.glob("00*.py"))[1:]:
        upgrade = path.read_text(encoding="utf-8").split("def downgrade")[0]
        patterns = (
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)\s+ON",
            r"ADD\s+CONSTRAINT\s+([a-z_][a-z0-9_]*)",
            r"op\.create_(?:index|unique_constraint|check_constraint)\(\s*[\"']([a-z0-9_]+)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, upgrade, re.IGNORECASE):
                found[match.group(1)] = path.name
    return found


# Foreign keys that migrations name but the models declare without a name. Same columns, target
# and ON DELETE behaviour; only the constraint name differs.
UNNAMED_IN_MODELS = {"fk_ai_invocations_thread"}


def test_every_index_and_constraint_a_migration_creates_is_declared_in_the_models() -> None:
    ddl = _model_ddl()
    missing = {name: source for name, source in _named_in_migrations().items() if name not in ddl and name not in UNNAMED_IN_MODELS}
    assert not missing, f"declare these in the models too: {missing}"


def test_the_two_partial_indexes_match_their_migrations() -> None:
    ddl = _model_ddl()
    assert "CREATE UNIQUE INDEX uq_integration_command_message ON conversation_commands (source_message_id) WHERE family = 'integration.execute'" in ddl
    assert "CREATE INDEX ix_outbox_delayed ON outbox_events (next_delivery_at, created_at) WHERE published_at IS NULL" in ddl
