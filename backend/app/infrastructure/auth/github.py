"""GitHub login identity resolution, separate from tenant-scoped connector authority."""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.infrastructure.auth.service import normalize_email
from app.infrastructure.database.models import (
    ExternalAuthIdentity,
    GitHubAuthFlow,
    GitHubOnboarding,
    HumanIdentity,
)

logger = logging.getLogger(__name__)


def integrity_metadata(error: IntegrityError) -> tuple[str, str]:
    # Driver metadata is safe to log; exception text, SQL and parameters are not.
    original = error.orig
    cause = getattr(original, "__cause__", None)
    state = getattr(original, "sqlstate", None) or getattr(cause, "sqlstate", None)
    constraint = getattr(original, "constraint_name", None) or getattr(
        cause, "constraint_name", None
    )
    state = state if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state) else "unknown"
    constraint = (
        constraint
        if isinstance(constraint, str) and re.fullmatch(r"[A-Za-z0-9_]{1,128}", constraint)
        else "unknown"
    )
    return state, constraint


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def check_flow[T: GitHubAuthFlow | GitHubOnboarding](
    row: T | None, statuses: set[str], *, verifier: str | None = None, user_id=None
) -> T:
    if not row or row.status not in statuses or row.expires_at <= datetime.now(UTC):
        raise HTTPException(400, "GitHub setup expired or was already consumed. Start again.")
    if verifier is not None and not secrets.compare_digest(
        getattr(row, "browser_challenge", ""), digest(verifier)
    ):
        raise HTTPException(400, "Complete GitHub sign-in in the browser that started it.")
    if user_id is not None and getattr(row, "user_id", None) != user_id:
        raise HTTPException(404, "GitHub setup was not found for this account.")
    return row


def verified_email(emails):
    for value in emails:
        if value.get("primary") is True and value.get("verified") is True:
            return normalize_email(value["email"])
    raise HTTPException(409, "Verify a primary email on GitHub before creating your account.")


async def resolve_identity(session, *, subject, email, name, purpose, linking_user_id=None):
    existing = await session.scalar(
        select(ExternalAuthIdentity).where(
            ExternalAuthIdentity.provider == "github",
            ExternalAuthIdentity.provider_subject == subject,
        )
    )
    if existing:
        if purpose == "link" and existing.user_id != linking_user_id:
            raise HTTPException(409, "This GitHub identity is linked to another Audoryn account.")
        return await session.get(HumanIdentity, existing.user_id)
    if purpose == "signin":
        raise HTTPException(
            409,
            "No account is linked to this GitHub identity. Choose Create account, or sign in with your password and link GitHub from Connections.",
        )
    if purpose == "link":
        identity = await session.get(HumanIdentity, linking_user_id)
        if not identity:
            raise HTTPException(401, "Sign in again before linking GitHub.")
        linked = await session.scalar(
            select(ExternalAuthIdentity.id).where(
                ExternalAuthIdentity.user_id == identity.id,
                ExternalAuthIdentity.provider == "github",
            )
        )
        if linked:
            raise HTTPException(409, "This Audoryn account already has another GitHub identity.")
    else:
        if not email:
            raise HTTPException(409, "A verified GitHub email is required to create your account.")
        email = normalize_email(email)
        if await session.scalar(select(HumanIdentity.id).where(HumanIdentity.email == email)):
            raise HTTPException(
                409,
                "An account with this email exists. Sign in with your password, then link GitHub from Connections.",
            )
        identity = HumanIdentity(
            id=uuid4(),
            subject="github:" + subject,
            email=email,
            display_name=(name or "")[:160] or None,
        )
    stage = "human_identity_insert" if purpose == "signup" else "external_identity_insert"
    try:
        if purpose == "signup":
            session.add(identity)
            # A UUID alone does not create a unit-of-work relationship. Persist the
            # parent before queuing its FK-dependent login identity in this transaction.
            await session.flush()
        stage = "external_identity_insert"
        session.add(
            ExternalAuthIdentity(user_id=identity.id, provider="github", provider_subject=subject)
        )
        await session.flush()
    except IntegrityError as error:
        sqlstate, constraint = integrity_metadata(error)
        logger.warning(
            "GitHub account persistence rejected stage=%s sqlstate=%s constraint=%s",
            stage,
            sqlstate,
            constraint,
        )
        await session.rollback()
        if (
            sqlstate == "23505"
            or getattr(error.orig, "sqlite_errorname", None) == "SQLITE_CONSTRAINT_UNIQUE"
        ):
            raise HTTPException(
                409, "The account changed during sign-in. Start GitHub sign-in again."
            ) from error
        raise HTTPException(
            503,
            "Account setup could not be saved. Please try again; contact support if it continues.",
        ) from error
    return identity
