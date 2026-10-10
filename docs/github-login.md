# GitHub login and automatic connector onboarding

## Scope and flow

The same GitHub App serves login and repository connections. Password authentication,
Redis bearer sessions and existing connector callbacks remain supported. No AI inference
or coding workspace is needed for authentication.

- Sign-in resolves `external_auth_identities` by stable numeric GitHub ID.
- Signup requires a verified primary GitHub email and creates a human identity only.
  It never creates a password, organization, worker grant or job.
- An email collision never automatically links identities. Sign in with the existing
  password, then choose **Link GitHub for sign-in** in Connections.
- Explicit linking verifies the initiating Audoryn session again at completion.
- Existing invitation and workspace onboarding run before connection attachment.
  Multiple workspaces require a choice. Current membership and `integrations.manage`
  are checked; invitees never gain permissions through signup.
- A verified personal installation matching the OAuth user connects automatically.
  Organization, multiple or suspended installations require explicit selection.
- If the App is not installed, optional onboarding offers **Continue to GitHub**.
  The backend correlates a single-use installation return. The frontend resumes the
  same workspace and automatically attaches the returned installation after live
  user/App/installation and membership checks. GitHub approval is never bypassed.
- **Skip for now** is available while checking, after errors, and before installation.
  It immediately returns to the workspace without signing out or disconnecting anything.
  Credential dismissal is requested in the background; if unavailable, a pending handoff
  can be offered again on a later sign-in and still expires normally.
- Login succeeds independently of connector configuration/access failures. Retry the
  connection or choose **Continue without connecting**. Dismissal clears pending
  credentials without disconnecting any existing installation.
- A later GitHub sign-in restores a still-valid unfinished signup/link handoff.
  If no handoff or owned active connection exists, it creates a fresh optional handoff
  using that login's authorization. Already connected users enter the workspace normally.
  Completed connections are reused by workspace, owner, GitHub user and installation.
  Reconnecting verifies the same identity, rechecks repository intersection and refreshes
  discovery. Connections owned by other people are never adopted.

## Required manual setup (not applied by implementation)

1. Apply [Neon SQL](sql/github_login_neon.sql) in Neon SQL Editor, **only** when
   `alembic_version` is `0014_preparation_retry`. Alternatively use Alembic locally;
   do not apply both. The script checks the prior revision and runs in a transaction.
2. Keep existing `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_ID`,
   `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_SLUG` and `INTEGRATION_ENCRYPTION_KEY`.
   No new key is needed. Never replace the encryption key for this feature.
3. Add a second **User authorization callback URL** to the existing GitHub App:
   `https://YOUR_BACKEND_HOST/api/v2/auth/github/callback`.
   Keep `/integrations/github/callback` for the existing connector.
4. Set **Post installation → Setup URL** on the existing GitHub App to:
   `https://YOUR_BACKEND_HOST/api/v2/auth/github/installation/callback`.
   This is a separate installation return, not another OAuth callback. Leave
   **Request user authorization (OAuth) during installation** unchecked: Audoryn
   already obtains user authorization during login before starting installation.
   **Redirect on update** may be enabled to return after repository access changes.
5. In the App's user permissions, grant **Email addresses: Read-only**.
   Keep current repository permissions. GitHub may require approval for a permission change.
6. Set these backend environment values, then restart:

```dotenv
GITHUB_LOGIN_ENABLED=true
GITHUB_LOGIN_CALLBACK_URL=https://YOUR_BACKEND_HOST/api/v2/auth/github/callback
GITHUB_FRONTEND_URL=http://localhost:5173
GITHUB_EXPANDED_ENABLED=true
INTEGRATION_FOUNDATION_ENABLED=true
```

For production use the exact HTTPS frontend origin for `GITHUB_FRONTEND_URL` and allow
it in the existing CORS configuration. No path, query, fragment or embedded credentials.
For a renewed tunnel, update the login callback in **both** environment and GitHub App
settings, in addition to the existing connector callback and event configuration.
Never put the backend tunnel into `GITHUB_FRONTEND_URL` unless it actually hosts the frontend.
The GitHub buttons remain hidden while login readiness is false.

## Security and recovery

OAuth state is hashed; backend PKCE and credential bundles are encrypted. The browser
keeps a separate secret in sessionStorage and sends only its SHA-256 challenge at start.
The callback returns a non-authoritative flow ID, never a GitHub token or Audoryn bearer.
The frontend removes the callback ID from the URL before exchange. Browser-secret proof
and a locked, expiring flow prevent replay and login CSRF.

The callback middleware strips OAuth and installation-return queries from the ASGI
query string before request/access logging. Provider diagnostics log category/type only. Upstream proxy logs
must also be configured to avoid recording OAuth callback queries.

The code exchange is checkpointed and the database transaction released before GitHub
network calls. Interrupted exchanges require restarting OAuth; single-use codes are not
replayed. Signup and linking do not require another login after connector failure.

Authorization has a 15-minute expiry; post-authentication connector handoff lasts one hour.
Expiry is enforced on every use. Expired encrypted handoffs are cleared lazily when a
GitHub authorization starts. Explicit dismissal clears them immediately. Existing connector
cleanup behavior and credential refresh remain in effect.

Sign-out revokes the Audoryn session only. Disconnecting the connector does not remove
its login identity mapping. Neither action uninstalls the GitHub App.

## Verification boundaries

Deterministic tests exercise identity collisions, browser/replay protection, callback
consumption, current membership/permissions, installation selection and repeated attachment.
Migration upgrade/downgrade is checked against an isolated SQLite database plus PostgreSQL
SQL generation; this is not a live Neon migration test. No production migration, credential
change, GitHub App settings change or live OAuth/installation write was performed.
After manual setup, verify new signup, later sign-in, password-account linking and denied
installation access with a controlled real browser session.

## Changed files

- Backend endpoints/registration: `app/api/github_auth_routes.py`, `app/api/github_routes.py`,
  `app/api/v2/router.py`, `app/bootstrap/application.py`, `app/bootstrap/settings.py`.
- Identity and connector services: `app/infrastructure/auth/github.py`,
  `app/infrastructure/auth/session_store.py`, `app/application/services/github_connector.py`,
  `app/execution/providers/native/github/authentication.py`, `app/infrastructure/database/models.py`.
- Frontend: `src/platform/authClient.ts`, `src/components/IdentityGate.tsx`,
  `src/components/GitHubSignupConnection.tsx`, `src/components/GitHubConnection.tsx`,
  `src/lib/githubSetup.ts`, `src/ProductApp.tsx`, `src/auth-page.css`.
- Verification/schema: `backend/tests/test_github_login.py`,
  `backend/tests/test_github_login_migration.py`, `frontend/tests/githubAuth.test.mjs`,
  `frontend/tests/githubSignupConnection.test.mjs`, `frontend/tests/githubConnection.test.mjs`,
  `backend/migrations/versions/0015_github_login.py`, `docs/sql/github_login_neon.sql`,
  `.env.example` and this document.


## Sign-in linked, repository installation still pending

The integration card checks the current user's saved GitHub login identity. Linked
users see a linked status rather than another sign-in linking button. This status
never implies repository access. An empty installation response shows installation
instructions instead of an empty selector; suspended installations cannot connect.

Set `GITHUB_APP_SLUG` to the name in `https://github.com/apps/<name>` to provide the
installation link. It is the App's URL name, not the user login or numeric App ID.
The signup/sign-in setup screen now finishes automatically on installation return;
configure the separate Setup URL above first. The integration card retains manual
installation refresh as a fallback for legacy connector onboarding. Do not create
another account. Signing in again offers fresh optional setup if the old handoff expired. Installation selection logs include matching/usable counts
and whether an installation URL is configured, without tokens or account content.


## Installation return and optional setup verification

The installation-start endpoint checks current integration permission, flow ownership,
workspace binding and expiry. It stores only the hash of a new correlation nonce in
`GitHubAuthFlow.state_hash`; OAuth is already consumed at that point. The Setup URL
consumes the nonce and redirects to the configured frontend with non-authoritative
flow/installation hints. It never creates a session, attaches a connection, or trusts
`installation_id` as proof of access. Authenticated continuation checks the current
GitHub installation list and the shared connector's exact verification and permission
rules before accepting it. A direct installation without state returns to Audoryn without
attaching anything. Stale, dismissed or replayed returns cannot revive old setup.

Read-only installation discovery releases its flow transaction while waiting for
GitHub, then reacquires and rechecks saved setup before connection attachment. Concurrent
skip or attachment is respected. Frontend requests share only in-flight checks; later
checks reread current state. Generation checks reject stale UI updates and navigation
following skip. The optional screen uses the existing GitHub/Audoryn marks, responsive
layout, keyboard focus indicators, and distinct loading, installation, selection and
error states. No dependency or database migration was added.

Local tests use deterministic providers and an isolated identity database. A browser
return still requires configuring the actual GitHub App Setup URL and testing with an
approved real account. Neither App settings nor credentials were changed by implementation.
