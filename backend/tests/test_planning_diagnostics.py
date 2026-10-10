import logging
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from app.runtime.planning_diagnostics import database_planning_error


@pytest.mark.parametrize("state,reason,retryable", [
    ("42P01", "database_schema_missing", False),
    ("42703", "database_schema_missing", False),
    ("42501", "database_permission_denied", False),
    ("08006", "database_preparation_failed", True),
])
def test_database_failure_logs_cause_without_sql_or_secrets(caplog, state, reason, retryable):
    class DriverError(Exception):
        sqlstate = state
    error = DBAPIError("secret SQL", {"token": "secret parameter"}, DriverError("secret body"))
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        converted = database_planning_error(error, work_item_id="item", run_id="run")
    assert converted.retryable == retryable
    assert converted.category == ("provider_unavailable" if retryable else "configuration_missing")
    assert reason in caplog.text and state in caplog.text
    assert "model=not_called" in caplog.text and "secret" not in caplog.text


def test_neon_sql_is_guarded_and_grants_runtime_only_table_access():
    sql = (Path(__file__).parents[2] / "docs/sql/0011_smart_planner_neon.sql").read_text()
    assert "SET LOCAL ROLE audoryn_migrator" in sql
    assert "Expected migration 0010_github_coding" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in sql and "TO audoryn_app" in sql
    assert sql.index("CREATE TABLE planner_decisions") < sql.index("CREATE TABLE planner_attempts")
    assert sql.index("UPDATE public.alembic_version") < sql.index("COMMIT")
    assert "DROP TABLE" not in sql and "GRANT ALL" not in sql
