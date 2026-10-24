# F26 deployment pipelines

## Frontend: GitHub → Vercel

The connected Vercel GitHub integration is the deployment mechanism.

- Pull request: Vercel Preview deployment.
- `main`: the staging web app (the `main` branch's preview, pointed at the staging API).
- Production: Vercel deploys the `production` branch, which the deploy workflow moves to the commit
  production runs (docs/deploy/pipeline.md).
- `deployment-contracts.yml` verifies that Vercel remains a Vite/frontend boundary and that
  backend-only secret names do not appear under `frontend/`.

No GitHub Actions Vercel token is required or stored.

## Backend: GitHub → Oracle (staging, then production)

`.github/workflows/deploy.yml` deploys every commit on `main` that passes CI to the staging VM, and a
commit proven on staging to production when it is run for production. It migrates each environment's
database from GitHub's runners, then calls the VM's `infrastructure/oracle/deploy.sh`. The details,
the setup and what to do when it stops: [docs/deploy/pipeline.md](deploy/pipeline.md).

The earlier Render workflow (`deploy-backend-render.yml`) was removed: the backend runs on Oracle.
