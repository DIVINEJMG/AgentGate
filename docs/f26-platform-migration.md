# F26 Platform Independence & Python Backend Migration

## Implemented in F26.0–F26.5

- Current GitHub state frozen on `audoryn-f25-appdeploy-reference`.
- Migration work isolated on `foundation/26-platform-independence-python`.
- Frontend moved under `frontend/`.
- Legacy TypeScript backend/tests preserved under `reference/`.
- Vercel configuration added for the React/Vite frontend.
- Platform-neutral frontend API/auth/realtime boundaries are being introduced.
- Active `backend/` path now contains the FastAPI skeleton.
- API v1/v2 system status contracts are represented in the new Python shell.

Render service/database/Redis provisioning is intentionally deferred to F26.7.
