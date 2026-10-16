"""A browser connection's live check (F34): open its start page through its own site rules, and on
request try its stored login, reporting what really happened. Values never leave this module."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.execution.browser.contracts import (
    BrowserElement,
    BrowserForm,
    BrowserLocator,
    BrowserObservation,
    BrowserSession,
)
from app.execution.browser.credentials import (
    ONE_TIME_CODE_FIELD,
    BrowserAuthenticationFailure,
    BrowserCredentialBundle,
    login_field_names,
)
from app.execution.browser.policy import BrowserDomainPolicy, BrowserNavigationBlocked
from app.execution.contracts import ProviderHealth, ResourceHealth

# The two fields the setup's login boxes store; a login saved as other fields cannot be tried
# without the person saying which box each one fills.
USERNAME_KEYS = ("username", "email")
PASSWORD_KEY = "password"
TEXT_INPUTS = {"text", "email", ""}
CODE_INPUTS = {"text", "tel", "number", ""}
CODE_LABEL = re.compile(
    r"one.?time|\botp\b|totp|2fa|mfa|two.?factor|verification|security code|auth\w* code|^\s*code\s*$",
    re.IGNORECASE,
)


class ProbeRuntime(Protocol):
    async def health(self) -> bool: ...
    async def create_session(
        self,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        run_id: UUID | None,
        ttl_seconds: int = 1800,
        navigation_policy: BrowserDomainPolicy | None = None,
    ) -> BrowserSession: ...
    async def navigate(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        operation: str,
        url: str | None = None,
        timeout_ms: int = 30_000,
    ) -> BrowserObservation: ...
    async def authenticate(
        self,
        session_id: UUID,
        *,
        organization_id: UUID,
        worker_id: UUID | None,
        bindings: tuple[tuple[BrowserLocator, str], ...],
        credentials: BrowserCredentialBundle,
        form_ref: str | None = None,
        timeout_ms: int = 15_000,
    ) -> BrowserObservation: ...
    async def close(self, session_id: UUID) -> BrowserSession: ...


@dataclass(frozen=True, slots=True)
class LoginForm:
    form: BrowserForm
    username_ref: str | None
    password_ref: str
    code_ref: str | None = None


def login_form(observation: BrowserObservation) -> LoginForm | None:
    """The page's login form, read from its structure: the form with a password field, the text or
    email field just before it, and a one-time-code box after it if there is one."""
    elements = {element.ref: element for element in observation.elements}
    for form in observation.form_details:
        fields = [elements[ref] for ref in form.field_refs if ref in elements]
        types = [(field.element_type or "").lower() for field in fields]
        if "password" not in types:
            continue
        index = types.index("password")
        username = next(
            (fields[i].ref for i in range(index - 1, -1, -1) if types[i] in TEXT_INPUTS), None
        )
        code = next((field.ref for field in fields[index + 1 :] if _is_code_box(field)), None)
        return LoginForm(
            form=form, username_ref=username, password_ref=fields[index].ref, code_ref=code
        )
    return None


def code_field(observation: BrowserObservation) -> tuple[BrowserForm, str] | None:
    """A form asking for a one-time code (and no password): the form and its code box."""
    if login_form(observation) is not None:
        return None
    elements = {element.ref: element for element in observation.elements}
    for form in observation.form_details:
        for ref in form.field_refs:
            if ref in elements and _is_code_box(elements[ref]):
                return form, ref
    return None


def _is_code_box(element: BrowserElement) -> bool:
    """A box for a one-time code, known by its name or label."""
    if (element.element_type or "").lower() not in CODE_INPUTS:
        return False
    return any(CODE_LABEL.search(label) for label in (element.field_name, element.name) if label)


def _reached(observation: BrowserObservation, policy: BrowserDomainPolicy) -> dict[str, object]:
    return {
        "url": observation.url,
        "title": observation.title[:200],
        "withinAllowedSites": policy.permits(observation.url).allowed,
    }


async def probe_connection(
    runtime: ProbeRuntime,
    *,
    organization_id: UUID,
    policy: BrowserDomainPolicy,
    start_url: str,
    credential: str | None,
    sign_in: bool,
) -> ProviderHealth:
    """Open the start page (and with `sign_in`, try the stored login) in a short session that is
    closed afterwards. The result says what was reached, in plain words."""
    started = time.monotonic()
    if not await runtime.health():
        return _health("unavailable", "The browser engine is not available on the server.", {})
    session = await runtime.create_session(
        organization_id=organization_id,
        worker_id=None,
        run_id=None,
        ttl_seconds=180,
        navigation_policy=policy,
    )
    session_id = session.id
    try:
        page = await runtime.navigate(
            session_id,
            organization_id=organization_id,
            worker_id=None,
            operation="navigation.open",
            url=start_url,
            timeout_ms=20_000,
        )
        reached = _reached(page, policy)
        details: dict[str, object] = {
            "reached": reached,
            "loginFields": login_field_names(credential),
        }
        if not sign_in:
            return _health(
                "healthy", f"Opened the start page: {page.title or page.url}.", details, started
            )
        outcome, message = await _sign_in(runtime, session_id, organization_id, page, credential)
        details["signIn"] = outcome
        return _health(
            "healthy" if outcome == "signed_in" else "degraded", message, details, started
        )
    except BrowserNavigationBlocked as error:
        return _health(
            "unavailable",
            f"The start page is outside this connection's rules: {error.decision.reason}",
            {"blocked": error.decision.target_url},
            started,
        )
    except Exception as error:  # noqa: BLE001 - any failure to load is reported, never raised
        return _health(
            "unavailable",
            f"The start page could not be opened ({type(error).__name__}).",
            {},
            started,
        )
    finally:
        await runtime.close(session_id)


async def _sign_in(
    runtime: ProbeRuntime,
    session_id: UUID,
    organization_id: UUID,
    page: BrowserObservation,
    credential: str | None,
) -> tuple[str, str]:
    """Try the stored login on the start page, and a one-time code if the site asks for one:
    (outcome, message)."""
    if not credential:
        return "no_login_stored", "No login is stored for this connection."
    form = login_form(page)
    if form is None:
        return (
            "no_login_form",
            "The start page has no login form (a password field) to sign in with.",
        )
    try:
        bundle = BrowserCredentialBundle.from_secret(credential)
        stored = bundle.keys()
        bindings = _login_bindings(form, stored)
        if isinstance(bindings, tuple):
            return bindings
        submit = _Submitter(runtime, session_id, organization_id, bundle)
        after = await submit(page, form.form, bindings)
        if login_form(after) is not None:
            return (
                "still_on_login",
                "After signing in, the login form is still showing: check the username and password.",
            )
        code = code_field(after)
        if code is not None:
            if ONE_TIME_CODE_FIELD not in stored:
                return _NEEDS_CODE
            after = await submit(after, code[0], [(code[1], ONE_TIME_CODE_FIELD)])
            if code_field(after) is not None:
                return (
                    "code_not_accepted",
                    "The one-time code was not accepted: check the stored otpSecret.",
                )
    except BrowserAuthenticationFailure as error:
        return "refused", f"The sign-in was not accepted: {error}"
    return "signed_in", f"Signed in: reached {after.title or after.url}."


_NEEDS_CODE = (
    "needs_code",
    "The site asks for a one-time code: store its secret as otpSecret under Other login fields.",
)


def _login_bindings(
    form: LoginForm, stored: tuple[str, ...]
) -> list[tuple[str, str]] | tuple[str, str]:
    """Which stored field fills which box, or the outcome when the login does not fit the form."""
    username_key = next((key for key in USERNAME_KEYS if key in stored), None)
    if PASSWORD_KEY not in stored or (form.username_ref is not None and username_key is None):
        return (
            "needs_fields",
            "The stored login has no username and password fields to fill this form with.",
        )
    bindings = [(form.password_ref, PASSWORD_KEY)]
    if form.username_ref is not None and username_key is not None:
        bindings.insert(0, (form.username_ref, username_key))
    if form.code_ref is not None:
        if ONE_TIME_CODE_FIELD not in stored:
            return _NEEDS_CODE
        bindings.append((form.code_ref, ONE_TIME_CODE_FIELD))
    return bindings


@dataclass(frozen=True, slots=True)
class _Submitter:
    runtime: ProbeRuntime
    session_id: UUID
    organization_id: UUID
    bundle: BrowserCredentialBundle

    async def __call__(
        self, page: BrowserObservation, form: BrowserForm, bindings: list[tuple[str, str]]
    ) -> BrowserObservation:
        return await self.runtime.authenticate(
            self.session_id,
            organization_id=self.organization_id,
            worker_id=None,
            credentials=self.bundle,
            bindings=tuple(
                (BrowserLocator(strategy="observation_ref", value=ref, observation_id=page.id), key)
                for ref, key in bindings
            ),
            form_ref=form.ref,
            timeout_ms=20_000,
        )


def _health(
    state: ResourceHealth, message: str, details: dict[str, object], started: float | None = None
) -> ProviderHealth:
    metadata = {"engine": "chromium", **details}
    if started is not None:
        metadata["seconds"] = round(time.monotonic() - started, 1)
    return ProviderHealth(
        state=state, message=message, checked_at=datetime.now(UTC), metadata=metadata
    )
