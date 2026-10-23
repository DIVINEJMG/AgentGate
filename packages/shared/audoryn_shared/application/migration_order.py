from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MigrationDomain:
    order: int
    name: str
    prerequisite: str | None = None


DOMAIN_MIGRATION_ORDER: tuple[MigrationDomain, ...] = (
    MigrationDomain(1, "system-bootstrap"),
    MigrationDomain(2, "authentication", "system-bootstrap"),
    MigrationDomain(3, "organizations-memberships", "authentication"),
    MigrationDomain(4, "agent-identity", "organizations-memberships"),
    MigrationDomain(5, "workforce", "agent-identity"),
    MigrationDomain(6, "jobs", "workforce"),
    MigrationDomain(7, "integrations", "jobs"),
    MigrationDomain(8, "capabilities", "integrations"),
    MigrationDomain(9, "policy", "capabilities"),
    MigrationDomain(10, "risk", "policy"),
    MigrationDomain(11, "action-gateway", "risk"),
    MigrationDomain(12, "approvals", "action-gateway"),
    MigrationDomain(13, "incidents", "approvals"),
    MigrationDomain(14, "audit", "incidents"),
    MigrationDomain(15, "scheduler-queue", "audit"),
    MigrationDomain(16, "runtime", "scheduler-queue"),
    MigrationDomain(17, "memory-artifacts", "runtime"),
    MigrationDomain(18, "results", "memory-artifacts"),
    MigrationDomain(19, "commercial", "results"),
    MigrationDomain(20, "supervision-performance", "commercial"),
)


def assert_migration_order(completed: set[str]) -> None:
    known = {domain.name for domain in DOMAIN_MIGRATION_ORDER}
    unknown = completed - known
    if unknown:
        raise ValueError(f"Unknown migration domains: {sorted(unknown)}")
    for domain in DOMAIN_MIGRATION_ORDER:
        if domain.name in completed and domain.prerequisite and domain.prerequisite not in completed:
            raise ValueError(
                f"{domain.name} cannot be authoritative before {domain.prerequisite}"
            )
