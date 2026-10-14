"""First-run setup for a new workspace.

The welcome questions (all optional) choose a starter worker, how careful workers should be,
and the user's working hours. Setup is built from fixed templates: no AI call, nothing is
scheduled and nothing runs until the person asks. Workspaces created before onboarding existed
have no onboarding row and never see any of this.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.governance_routes import _create_policy
from app.api.product_common import append_audit
from app.api.workforce_routes import (
    DEFAULT_HOURS,
    automatic_agent,
    create_role,
    create_worker,
    set_worker_status,
)
from app.application.services.worker_memory import WorkerMemoryService
from app.domain.identity.principals import HumanPrincipal
from app.execution.bootstrap import execution_provider_registry
from app.infrastructure.database.models import (
    Approval,
    ConversationMessage,
    ConversationThread,
    Integration,
    IntegrationEventSubscription,
    Job,
    JobRevision,
    OrganizationOnboarding,
    WorkforceRole,
    WorkItem,
)

TOOLS = {
    "github": "GitHub",
    "slack": "Slack",
    "gmail": "Gmail",
    "google_calendar": "Google Calendar",
    "google_drive": "Google Drive",
    "web_research": "Web search",
}
CARE = ("visible", "changes", "risky")

# One starter worker per goal. Examples are what the welcome message suggests asking; the ones
# for tools the person uses come first.
GOALS: dict[str, dict[str, Any]] = {
    "engineering": {
        "name": "Engineering Assistant",
        "role": "Engineering assistant",
        "department": "Engineering",
        "charter": "Keeps an eye on your repositories: summarises issues and pull requests, "
                   "answers questions about the code and drafts changes for review.",
        "responsibilities": ["Summarise new issues and pull requests", "Answer questions about the code",
                             "Draft code changes for review"],
        "instructions": ["Explain what you found and where, with links.",
                         "Propose changes as drafts for a person to review."],
        "examples": {
            "github": ["Summarise the open issues in my repository",
                       "Tell me about every new issue as soon as it is opened"],
            "slack": ["Post a short daily summary of new pull requests to Slack"],
            "": ["What can you help me with?"],
        },
    },
    "support": {
        "name": "Support Assistant",
        "role": "Support assistant",
        "department": "Support",
        "charter": "Helps your support work: sorts incoming requests, drafts replies and keeps "
                   "track of what still needs an answer.",
        "responsibilities": ["Sort incoming requests", "Draft replies for review", "Track open questions"],
        "instructions": ["Draft replies for a person to review before anything is sent.",
                         "Keep a friendly, clear tone."],
        "examples": {
            "gmail": ["Summarise the unread support emails from today",
                      "Draft replies to the three oldest unanswered emails"],
            "slack": ["Summarise this week's questions in our support channel"],
            "": ["What can you help me with?"],
        },
    },
    "research": {
        "name": "Research Assistant",
        "role": "Research assistant",
        "department": "Research",
        "charter": "Finds and summarises information from the web and your documents, always "
                   "with sources.",
        "responsibilities": ["Research questions on the web", "Summarise documents", "Cite every source"],
        "instructions": ["Cite the source for every claim.", "Say clearly when something is uncertain."],
        "examples": {
            "web_research": ["Research the latest news about our industry and summarise it with sources"],
            "google_drive": ["Summarise the documents in my research folder"],
            "": ["What can you help me with?"],
        },
    },
    "operations": {
        "name": "Operations Assistant",
        "role": "Operations assistant",
        "department": "Operations",
        "charter": "Keeps day-to-day operations moving: tracks tasks, prepares summaries and "
                   "follows up on what is waiting.",
        "responsibilities": ["Prepare daily summaries", "Track open tasks", "Follow up on waiting work"],
        "instructions": ["Keep summaries short and specific.", "Flag anything that needs a decision."],
        "examples": {
            "google_calendar": ["Summarise my meetings for tomorrow"],
            "slack": ["Every weekday at 9:00, post a summary of yesterday's open tasks to Slack"],
            "": ["What can you help me with?"],
        },
    },
    "general": {
        "name": "Workspace Assistant",
        "role": "Workspace assistant",
        "department": "General",
        "charter": "Your first worker: helps with research, summaries and tasks across the tools "
                   "you connect.",
        "responsibilities": ["Answer questions", "Prepare summaries", "Carry out tasks you assign"],
        "instructions": ["Explain what you did and where.", "Ask when a request is unclear."],
        "examples": {
            "github": ["Summarise the open issues in my repository"],
            "web_research": ["Research a topic on the web and summarise it with sources"],
            "": ["What can you help me with?"],
        },
    },
}

CARE_SENTENCES = {
    "visible": "Before I do anything other people can see, such as posting a comment or sending "
               "a message, I'll ask you first unless you asked for it.",
    "changes": "Before I make any change, I'll ask you first.",
    "risky": "I'll only stop to ask before actions that can't be undone.",
}


def clean_answers(raw: dict[str, Any] | None) -> dict[str, Any]:
    """The answers with anything unknown dropped; skipped questions get the default."""
    raw = raw if isinstance(raw, dict) else {}
    goal = str(raw.get("goal") or "")
    care = str(raw.get("care") or "")
    tools = [str(tool) for tool in raw.get("tools") or [] if str(tool) in TOOLS]
    timezone = str(raw.get("timezone") or "UTC")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        timezone = "UTC"
    return {
        "goal": goal if goal in GOALS else "general",
        "tools": list(dict.fromkeys(tools)),
        "care": care if care in CARE else "visible",
        "timezone": timezone,
    }


def examples_for(answers: dict[str, Any]) -> list[str]:
    template = GOALS[answers["goal"]]["examples"]
    ordered = [example for tool in answers["tools"] for example in template.get(tool, [])]
    ordered += [example for tool, items in template.items() if tool and tool not in answers["tools"]
                for example in items]
    return list(dict.fromkeys(ordered + template[""]))[:3]


def welcome_message(answers: dict[str, Any], worker_name: str, person: str) -> str:
    template = GOALS[answers["goal"]]
    hello = f"Hi {person}! " if person else "Hi! "
    tools = [TOOLS[tool] for tool in answers["tools"]]
    connect = (f" Connect {', '.join(tools[:-1]) + ' and ' + tools[-1] if len(tools) > 1 else tools[0]} "
               "from Connections and I can work there too." if tools else "")
    examples = "\n".join(f"• {example}" for example in examples_for(answers))
    return (f"{hello}I'm {worker_name}, your first worker. {template['charter']}{connect}\n\n"
            f"Here are a few things you can ask me:\n{examples}\n\n"
            f"Nothing runs until you ask. {CARE_SENTENCES[answers['care']]}")


def _hours(timezone: str) -> dict[str, Any]:
    return {**DEFAULT_HOURS, "timezone": timezone}


def _side_effect_scopes(*, visible_only: bool) -> list[str]:
    scopes = set()
    for provider in execution_provider_registry().providers():
        for capability in provider.manifest.capabilities:
            if capability.side_effect and (not visible_only or capability.mode == "write"):
                scopes.add(capability.scope)
    return sorted(scopes)


async def _care_policy(session: AsyncSession, organization_id: UUID, principal: HumanPrincipal, care: str) -> str | None:
    """The approval rule matching the "how careful" answer. The default needs no rule: writes
    other people can see already ask first unless the request asked for them."""
    if care == "changes":
        payload = {"name": "Ask before any change (set up when the workspace was created)",
                   "effect": "require_approval", "priority": 150,
                   "description": "Every action that changes something waits for a person's approval. "
                                  "Use 'Always allow' on an approval to let a specific action run.",
                   "selectors": {"scopes": _side_effect_scopes(visible_only=False)}}
    elif care == "risky":
        payload = {"name": "Run everyday writes without asking (set up when the workspace was created)",
                   "effect": "allow", "priority": 150,
                   "description": "Comments, messages and other everyday writes run without asking. "
                                  "Actions that can't be undone (high or critical risk) still ask.",
                   "selectors": {"scopes": _side_effect_scopes(visible_only=True), "risks": ["low", "medium"]}}
    else:
        return None
    if not payload["selectors"]["scopes"]:
        return None
    policy = await _create_policy(session, organization_id, principal, payload)
    return str(policy.id)


async def _role(session: AsyncSession, organization_id: UUID, principal: HumanPrincipal, template: dict[str, Any]) -> WorkforceRole:
    role = await session.scalar(select(WorkforceRole).where(
        WorkforceRole.organization_id == organization_id,
        func.lower(WorkforceRole.name) == template["role"].lower()))
    if role is not None:
        return role
    return await create_role(session, organization_id, principal, {
        "name": template["role"], "purpose": template["charter"],
        "defaultInstructions": "\n".join(template["instructions"]),
        "defaultResponsibilities": template["responsibilities"], "recommendedCapabilities": []})


async def set_up_workspace(session: AsyncSession, organization_id: UUID, principal: HumanPrincipal,
                           raw_answers: dict[str, Any] | None, person: str = "") -> OrganizationOnboarding:
    """Builds the starter setup from the answers. Safe to repeat: each finished part is saved,
    so a retry continues where it stopped, and a finished setup is returned as it is."""
    row = await session.scalar(select(OrganizationOnboarding).where(
        OrganizationOnboarding.organization_id == organization_id))
    if row is None:
        raise LookupError("This workspace has no first-run setup.")
    if row.state == "ready":
        return row
    answers = clean_answers(raw_answers)
    template = GOALS[answers["goal"]]
    setup = dict(row.setup or {})
    row.answers = answers

    if not setup.get("workerId"):
        role = await _role(session, organization_id, principal, template)
        agent, _credential = await automatic_agent(session, organization_id, principal, template["name"])
        worker = await create_worker(session, organization_id, principal, {
            "agentIdentityId": str(agent.id), "roleId": str(role.id), "name": template["name"],
            "department": template["department"], "description": template["charter"],
            "responsibilities": template["responsibilities"],
            "instructions": "\n".join(template["instructions"]), "workingHours": _hours(answers["timezone"]),
        }, provisioning="automatic")
        await set_worker_status(session, organization_id, worker.id, principal, "active")
        await WorkerMemoryService(session).seed_identity(
            worker=worker, role=template["role"], charter=template["charter"],
            responsibilities=template["responsibilities"], source_id=f"onboarding:{row.id}")
        setup.update(workerId=str(worker.id), workerName=worker.name)
        row.setup = setup
        await session.commit()

    if "policyId" not in setup:
        setup["policyId"] = await _care_policy(session, organization_id, principal, answers["care"])
        row.setup = dict(setup)
        await session.commit()

    if not setup.get("threadId"):
        thread = ConversationThread(organization_id=organization_id, worker_id=UUID(setup["workerId"]),
                                    title=f"Welcome to {setup['workerName']}", status="active",
                                    created_by=principal.user_id)
        session.add(thread)
        await session.flush()
        session.add(ConversationMessage(organization_id=organization_id, thread_id=thread.id, role="worker",
                                        content=welcome_message(answers, setup["workerName"], person),
                                        artifact_references=[], command_references=[], result_references=[]))
        setup["threadId"] = str(thread.id)

    row.setup, row.state = dict(setup), "ready"
    await append_audit(session, organization_id=organization_id, principal=principal,
        event_type="organization.onboarding.completed", category="workspace", resource_type="organization",
        resource_id=str(organization_id), outcome="ready",
        summary=f"Workspace set up with {setup['workerName']}.", metadata={"answers": answers})
    await session.commit()
    return row


async def checklist(session: AsyncSession, organization_id: UUID, answers: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The five getting-started steps, each worked out from what exists, in one query."""
    org = organization_id
    connected = exists().where(Integration.organization_id == org, Integration.status == "connected",
                               Integration.provider.not_in(("browser", "web_research")))
    said_hi = exists().where(ConversationMessage.organization_id == org, ConversationMessage.role == "human")
    ran = exists().where(WorkItem.organization_id == org)
    reviewed = exists().where(Approval.organization_id == org, Approval.status.in_(("approved", "rejected")))
    scheduled = exists().where(
        Job.organization_id == org, Job.status == "active", JobRevision.job_id == Job.id,
        JobRevision.revision == Job.current_revision,
        JobRevision.definition["triggerConfig"]["schedule"]["enabled"].as_boolean().is_(True))
    triggered = exists().where(IntegrationEventSubscription.organization_id == org,
                               IntegrationEventSubscription.enabled.is_(True))
    row = (await session.execute(select(connected, said_hi, ran, reviewed, scheduled, triggered))).one()
    tools = (answers or {}).get("tools") or []
    first_tool = next((tool for tool in tools if tool != "web_research"), None)
    return [
        {"id": "connect", "label": "Connect a tool", "complete": bool(row[0]),
         "description": "Give your worker access to an account, such as GitHub or Slack.",
         "destination": f"connections/directory/{first_tool.replace('_', '-')}" if first_tool else "connections/directory"},
        {"id": "say_hi", "label": "Say hi to your worker", "complete": bool(row[1]),
         "description": "Send your first message in Conversations.", "destination": "conversations"},
        {"id": "first_task", "label": "Run a first task", "complete": bool(row[2]),
         "description": "Ask your worker to do something and watch it work.", "destination": "conversations"},
        {"id": "approval", "label": "Review an approval", "complete": bool(row[3]),
         "description": "Approve or decline an action your worker asked about.", "destination": "inbox"},
        {"id": "schedule", "label": "Set a schedule or a trigger", "complete": bool(row[4] or row[5]),
         "description": "Let work start on its own, at set times or when something happens.", "destination": "jobs"},
    ]


async def onboarding_public(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    row = await session.scalar(select(OrganizationOnboarding).where(
        OrganizationOnboarding.organization_id == organization_id))
    if row is None:
        return {"state": "none"}
    result: dict[str, Any] = {"state": row.state, "answers": row.answers or {}, "setup": row.setup or {},
                              "checklistDismissed": row.checklist_dismissed}
    if row.state == "ready":
        steps = await checklist(session, organization_id, row.answers)
        result["checklist"] = {"steps": steps, "complete": sum(step["complete"] for step in steps), "total": len(steps)}
        result["examples"] = examples_for(clean_answers(row.answers))
    return result
