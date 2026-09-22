from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OrganizationRecord:
    id: UUID
    name: str
    created_by: UUID


class OrganizationRepository(Protocol):
    async def create(self, *, name: str, created_by: UUID) -> OrganizationRecord: ...
    async def get(self, organization_id: UUID) -> OrganizationRecord | None: ...
    async def list_for_user(self, user_id: UUID) -> list[OrganizationRecord]: ...
