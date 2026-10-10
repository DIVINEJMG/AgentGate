import importlib.util
from io import StringIO
from pathlib import Path
from typing import Any, cast

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.infrastructure.database.models import PlannerAttempt, PlannerDecision


def test_offline_upgrade_downgrade_and_model_constraints():
    path = Path(__file__).parents[1] / "migrations/versions/0011_smart_planner.py"
    spec = importlib.util.spec_from_file_location("planner_migration", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        module.upgrade()
        module.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE planner_decisions" in sql
    assert "CREATE TABLE planner_attempts" in sql
    assert "uq_planner_pending_decision" in sql
    assert "uq_planner_route_attempt" in sql
    assert sql.index("DROP TABLE planner_attempts") < sql.index("DROP TABLE planner_decisions")
    for model in (PlannerAttempt, PlannerDecision):
        ddl = str(CreateTable(cast(Any, model.__table__)).compile(dialect=postgresql.dialect()))
        assert "organization_id UUID NOT NULL" in ddl
        assert "JSONB" in ddl or model is PlannerAttempt
