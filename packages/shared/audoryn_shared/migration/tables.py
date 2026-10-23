from sqlalchemy import Column, DateTime, ForeignKey, MetaData, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

migration_metadata = MetaData()

migration_batches = Table(
    "migration_batches",
    migration_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("source_system", String(64), nullable=False),
    Column("status", String(24), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

migration_staging_records = Table(
    "migration_staging_records",
    migration_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        UUID(as_uuid=True),
        ForeignKey("migration_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("entity_type", String(64), nullable=False),
    Column("legacy_id", String(255), nullable=False),
    Column("new_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("validation_status", String(24), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "batch_id",
        "entity_type",
        "legacy_id",
        name="uq_migration_staging_legacy",
    ),
)

migration_id_mappings = Table(
    "migration_id_mappings",
    migration_metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "batch_id",
        UUID(as_uuid=True),
        ForeignKey("migration_batches.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("entity_type", String(64), nullable=False),
    Column("legacy_id", String(255), nullable=False),
    Column("new_id", UUID(as_uuid=True), nullable=False),
    Column("source_system", String(64), nullable=False),
    Column("migrated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "source_system",
        "entity_type",
        "legacy_id",
        name="uq_migration_source_identity",
    ),
)
