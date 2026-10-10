import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def migration():
    path = Path(__file__).parents[1] / "migrations/versions/0015_github_login.py"
    spec = importlib.util.spec_from_file_location("github_login_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_constraints_and_downgrade_on_isolated_database():
    engine = create_engine("sqlite://")
    change = migration()
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        change.upgrade()
        assert set(inspect(connection).get_table_names()) == {
            "external_auth_identities",
            "github_auth_flows",
        }
        assert (
            inspect(connection).get_indexes("github_auth_flows")[0]["name"]
            == "ix_github_auth_expiry"
        )
        values = {"id": "a", "user": "person", "provider": "github", "subject": "123"}
        insert = text(
            "INSERT INTO external_auth_identities VALUES (:id,:user,:provider,:subject,'2026-10-09','2026-10-09')"
        )
        connection.execute(insert, values)
        with pytest.raises(IntegrityError):
            connection.execute(insert, {**values, "id": "b", "user": "other"})
        change.downgrade()
        assert inspect(connection).get_table_names() == []


def test_neon_sql_is_version_guarded_and_matches_added_schema():
    sql = (Path(__file__).parents[2] / "docs/sql/github_login_neon.sql").read_text()
    assert "RAISE EXCEPTION" in sql
    assert migration().down_revision in sql and migration().revision in sql
    for name in (
        "external_auth_identities",
        "github_auth_flows",
        "uq_external_provider_subject",
        "uq_external_user_provider",
        "ck_github_auth_purpose",
        "ix_github_auth_expiry",
    ):
        assert name in sql
    assert "credential_ciphertext TEXT" in sql
    assert "UPDATE human_identities" not in sql


def test_postgres_upgrade_and_downgrade_sql_generation():
    from io import StringIO

    change = migration()
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        change.upgrade()
        change.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE external_auth_identities" in sql
    assert "REFERENCES human_identities (id) ON DELETE CASCADE" in sql
    assert "CREATE INDEX ix_github_auth_expiry" in sql
    assert "DROP TABLE github_auth_flows" in sql
