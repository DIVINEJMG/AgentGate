from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.auth.passwords import hash_password, verify_password
from app.infrastructure.auth.session_store import RedisSessionStore
from app.infrastructure.database.models import HumanIdentity, LocalAuthCredential


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: UUID
    email: str
    name: str | None


class AuthenticationFailure(Exception):
    pass


class RegistrationFailure(Exception):
    pass


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 320 or "@" not in email:
        raise RegistrationFailure("Enter a valid email address.")
    local, _, domain = email.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise RegistrationFailure("Enter a valid email address.")
    return email


class LocalAuthenticationService:
    def __init__(self, session: AsyncSession, sessions: RedisSessionStore) -> None:
        self._session = session
        self._sessions = sessions

    async def register(self, *, email: str, password: str, name: str | None) -> tuple[AuthenticatedUser, str]:
        normalized_email = normalize_email(email)
        display_name = (name or "").strip() or None
        if display_name is not None and len(display_name) > 160:
            raise RegistrationFailure("Name must be 160 characters or fewer.")
        try:
            encoded = hash_password(password)
        except ValueError as exc:
            raise RegistrationFailure(str(exc)) from exc

        existing = await self._session.scalar(
            select(HumanIdentity).where(HumanIdentity.email == normalized_email)
        )
        if existing is not None:
            raise RegistrationFailure("An account with that email already exists.")

        identity = HumanIdentity(
            subject="pending-local-subject",
            email=normalized_email,
            display_name=display_name,
        )
        self._session.add(identity)
        await self._session.flush()
        identity.subject = f"local:{identity.id}"

        credential = LocalAuthCredential(user_id=identity.id, password_hash=encoded)
        self._session.add(credential)
        await self._session.commit()

        token = await self._sessions.create(identity.id)
        return AuthenticatedUser(identity.id, normalized_email, display_name), token

    async def login(self, *, email: str, password: str) -> tuple[AuthenticatedUser, str]:
        try:
            normalized_email = normalize_email(email)
        except RegistrationFailure as exc:
            raise AuthenticationFailure("Invalid email or password.") from exc

        identity = await self._session.scalar(
            select(HumanIdentity).where(HumanIdentity.email == normalized_email)
        )
        credential = None
        if identity is not None:
            credential = await self._session.scalar(
                select(LocalAuthCredential).where(LocalAuthCredential.user_id == identity.id)
            )

        if (
            identity is None
            or credential is None
            or not verify_password(password, credential.password_hash)
        ):
            failures = await self._sessions.record_failed_login(normalized_email)
            if failures >= 10:
                raise AuthenticationFailure("Too many failed sign-in attempts. Try again later.")
            raise AuthenticationFailure("Invalid email or password.")

        await self._sessions.clear_failed_login(normalized_email)
        token = await self._sessions.create(identity.id)
        return AuthenticatedUser(identity.id, normalized_email, identity.display_name), token

    async def current_user(self, token: str) -> AuthenticatedUser | None:
        record = await self._sessions.get(token)
        if record is None:
            return None
        identity = await self._session.get(HumanIdentity, record.user_id)
        if identity is None or identity.email is None:
            await self._sessions.revoke(token)
            return None
        return AuthenticatedUser(identity.id, identity.email, identity.display_name)

    async def logout(self, token: str) -> None:
        await self._sessions.revoke(token)
