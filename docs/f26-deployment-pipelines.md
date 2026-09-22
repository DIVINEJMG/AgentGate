# F26 deployment pipelines

## Frontend: GitHub → Vercel

The connected Vercel GitHub integration is the deployment mechanism.

- Pull request: Vercel Preview deployment.
- Merge to `main`: Vercel Production deployment.
- `deployment-contracts.yml` verifies that Vercel remains a Vite/frontend boundary and that
  backend-only secret names do not appear under `frontend/`.

No GitHub Actions Vercel token is required or stored.

## Backend: GitHub → Render

`deploy-backend-render.yml` runs only after the GitHub CI workflow succeeds on `main` (or by
manual dispatch). It triggers Render only when repository secret `RENDER_DEPLOY_HOOK_URL` exists.

Current Render PostgreSQL and Redis resources are provisioned. Paid API/worker/cron compute is not
provisioned, so no deploy hook is configured yet. The workflow skips cleanly until compute is
explicitly enabled.

When Render compute is enabled, deployment order must remain:

1. CI passes.
2. Alembic migrations are validated.
3. Render deployment starts.
4. API/worker/scheduler use the same migration-compatible release.

Runtime execution remains disabled until runtime parity/cutover is complete.
