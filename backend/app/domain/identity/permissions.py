ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset({
        "organizations.read","organizations.manage","memberships.manage","agents.read","agents.manage",
        "workforce.read","workforce.manage","jobs.read","jobs.manage","jobs.run","supervision.read",
        "performance.read","supervision.manage","memory.read","memory.manage","artifacts.read",
        "results.read","results.export","integrations.read","integrations.manage","capabilities.read",
        "capabilities.manage","policies.read","policies.manage","actions.read","actions.manage",
        "approvals.review","audit.read","risk.read","incidents.read","incidents.manage",
    }),
    "admin": frozenset({
        "organizations.read","organizations.manage","memberships.manage","agents.read","agents.manage",
        "workforce.read","workforce.manage","jobs.read","jobs.manage","jobs.run","supervision.read",
        "performance.read","supervision.manage","memory.read","memory.manage","artifacts.read",
        "results.read","results.export","integrations.read","integrations.manage","capabilities.read",
        "capabilities.manage","policies.read","policies.manage","actions.read","actions.manage",
        "approvals.review","audit.read","risk.read","incidents.read","incidents.manage",
    }),
    "security_manager": frozenset({
        "organizations.read","agents.read","agents.manage","workforce.read","jobs.read","supervision.read",
        "performance.read","supervision.manage","memory.read","artifacts.read","results.read",
        "results.export","integrations.read","integrations.manage","capabilities.read","capabilities.manage",
        "policies.read","policies.manage","actions.read","actions.manage","approvals.review","audit.read",
        "risk.read","incidents.read","incidents.manage",
    }),
    "operator": frozenset({
        "organizations.read","agents.read","agents.manage","workforce.read","workforce.manage","jobs.read",
        "jobs.manage","jobs.run","supervision.read","performance.read","supervision.manage","memory.read",
        "memory.manage","artifacts.read","results.read","results.export","integrations.read",
        "capabilities.read","capabilities.manage","policies.read","actions.read","actions.manage",
        "audit.read","risk.read","incidents.read","incidents.manage",
    }),
    "approver": frozenset({
        "organizations.read","agents.read","workforce.read","jobs.read","supervision.read",
        "performance.read","memory.read","artifacts.read","results.read","results.export",
        "integrations.read","capabilities.read","policies.read","actions.read","approvals.review",
        "audit.read","risk.read","incidents.read",
    }),
    "viewer": frozenset({
        "organizations.read","agents.read","workforce.read","jobs.read","supervision.read",
        "performance.read","memory.read","artifacts.read","results.read","integrations.read",
        "capabilities.read","policies.read","actions.read","audit.read","risk.read","incidents.read",
    }),
}


def permissions_for_role(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())
