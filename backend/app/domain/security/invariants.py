from app.domain.identity.principals import AgentPrincipal, HumanPrincipal


def require_human_approver(principal: HumanPrincipal | AgentPrincipal) -> HumanPrincipal:
    if isinstance(principal, AgentPrincipal):
        raise PermissionError("Agent cannot approve its own or another Agent action.")
    return principal


def require_execution_allowed(*, incident_suspended: bool, resumed: bool, security_revalidated: bool) -> None:
    if incident_suspended:
        raise PermissionError("Incident kill switch overrides execution.")
    if resumed and not security_revalidated:
        raise PermissionError("Resumed work must revalidate security before execution.")


def require_job_authority(*, capability_granted: bool, job_requires_capability: bool) -> None:
    if job_requires_capability and not capability_granted:
        raise PermissionError("Job requirement does not independently authorize execution.")
