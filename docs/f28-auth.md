# F28 Cross-Service Authentication

Authentication is now implemented across the active Aduoryn stack.

## Flow

Vercel frontend -> Render FastAPI -> Neon + Upstash Redis

- Neon stores human identities, salted password hashes, organization memberships, and roles.
- Upstash Redis stores revocable opaque sessions and distributed login-throttling state.
- Render exposes signup, login, session, logout, and organization onboarding APIs.
- Vercel sends bearer sessions through the existing API client.
- Runtime authorization resolves organization membership and role permissions from the canonical database.

## Active endpoints

- POST /api/v2/auth/signup
- POST /api/v2/auth/login
- GET /api/v2/auth/session
- POST /api/v2/auth/logout
- GET/POST /api/v1/organizations
- GET/POST /api/v2/organizations

## Security properties

- Raw passwords are never stored.
- Passwords use salted scrypt hashes.
- Session tokens are opaque and revocable.
- Session state is non-authoritative Redis coordination state.
- Human identity and membership remain canonical in Neon.
- CORS allows the active Vercel frontend and localhost development origin.
