from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.organizations.repositories import OrganizationRecord, OrganizationRepository
from app.infrastructure.database.models import Organization, OrganizationMembership


class SQLAlchemyOrganizationRepository(OrganizationRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _record(model: Organization) -> OrganizationRecord:
        return OrganizationRecord(id=model.id, name=model.name, created_by=model.created_by)

    async def create(self, *, name: str, created_by: UUID) -> OrganizationRecord:
        model = Organization(name=name, created_by=created_by)
        self._session.add(model)
        await self._session.flush()
        return self._record(model)

    async def get(self, organization_id: UUID) -> OrganizationRecord | None:
        model = await self._session.get(Organization, organization_id)
        return self._record(model) if model else None

    async def list_for_user(self, user_id: UUID) -> list[OrganizationRecord]:
        statement = (
            select(Organization)
            .join(OrganizationMembership)
            .where(OrganizationMembership.user_id == user_id)
            .order_by(Organization.name, Organization.id)
        )
        models = (await self._session.scalars(statement)).all()
        return [self._record(model) for model in models]
