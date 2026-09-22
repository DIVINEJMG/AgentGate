from app.infrastructure.database.base import Base
from app.infrastructure.database.models import Organization, WorkItem
from app.infrastructure.redis.coordination import RedisCoordinator

def test_canonical_schema_contains_expected_tables() -> None:
    expected = {"organizations","organization_memberships","human_identities","agent_identities","agent_credentials","workers","workforce_roles","jobs","job_revisions","work_items","runs","run_steps","integrations","integration_credentials","capability_profiles","policies","policy_revisions","actions","approvals","risk_events","incidents","execution_controls","memories","artifacts","results","result_versions","result_exports","audit_events","outbox_events"}
    assert expected <= set(Base.metadata.tables)

def test_relational_constraints_exist() -> None:
    assert Organization.__table__.c.created_by.foreign_keys
    assert WorkItem.__table__.c.job_id.foreign_keys
    assert WorkItem.__table__.c.correlation_id.unique

def test_redis_is_coordination_boundary() -> None:
    public = set(dir(RedisCoordinator))
    assert {"acquire_lock","release_lock","heartbeat","cache_set","cache_get","publish"} <= public
    assert "save_organization" not in public
