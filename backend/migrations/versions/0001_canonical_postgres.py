"""Establish canonical PostgreSQL schema."""

from alembic import op

from audoryn_shared.infrastructure.database import models  # noqa: F401
from audoryn_shared.infrastructure.database.base import Base

revision = "0001_canonical_postgres"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=False)
