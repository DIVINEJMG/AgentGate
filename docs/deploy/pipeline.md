# The deploy pipeline: staging, then production

New code reaches people in two steps, without opening a shell on Oracle:

```
PR → CI → merge to main
  → automatically, on the STAGING VM: migrate staging's database → build what changed → switch → check
  → you run "Deploy" for production in GitHub (and approve it): the same commit, already proven on staging
  → on the PRODUCTION VM: migrate production's database → build → switch (core copies one at a time) → check
  → the `production` branch moves to that commit, and Vercel deploys the web app from it
  → a failed check rolls the VM back to the version it ran before, by itself
```

The Oracle shell stays for what a pipeline should not do: changing secrets or env values, changing the service layout (`infrastructure/oracle/quadlet/`), first-time setup, and incidents.

## How it works

| Part | Where | What it does |
|---|---|---|
| `.github/workflows/deploy.yml` | GitHub | Picks the environment and commit, migrates that environment's database, calls the VM, records what runs |
| `infrastructure/oracle/deploy.sh` | each VM | The only command GitHub's deploy key may run: `deploy <commit>`, `rollback`, `status` |
| `infrastructure/oracle/check_services.py` | each VM | The checks after every switch; a failure means a rollback |

**Staging** gets every commit on `main` that passes CI, once the repository variable `DEPLOY_STAGING` is `true`.

**Production** gets a commit only when you run the workflow ("Actions → Deploy → Run workflow", environment `production`). Leave the commit empty to promote what staging runs now. A commit staging never ran successfully is refused. With a required reviewer on the `production` environment, GitHub also asks for your approval before anything runs.

**On the VM, `deploy <commit>`:**
1. refuses a commit that is not on `origin/main`, a checkout with local changes, and a release that changes the service layout (that one is a hand deploy);
2. works out which images the release changes: `core` (`backend/`, `packages/`), `execution` (`packages/`, `services/execution/`), `gateway` (`services/gateway/`). Tests and docs change none;
3. builds them, tagged with the commit, **before anything restarts**. A failed build changes nothing that runs;
4. switches, in this order:
   - the execution service: running steps get their 2-minute drain;
   - the core copies: one at a time, with no downtime;
   - the gateway: a few seconds of 502;
5. runs `check_services.py`. If any check fails, it switches back to the previous images and commit and checks again;
6. keeps the images of the last three deployed commits, so a rollback needs no rebuild.

**Rollback:** run the workflow with action `rollback` for that environment. The VM returns to the commit it deployed before the current one. Running it again returns to where you were. Migrations are not undone: they are written so the previous code still works with them (below).

**The web app:** Vercel deploys production from the `production` branch, which the workflow moves to the commit production runs, after a deploy or a rollback. Pull requests get Vercel previews as before. The `main` branch's preview is the staging web app, pointed at the staging API.

### Rules for every release

- **Migrations go first and must not break the running code:** add a column or table in one release, use it in the next; remove something only once no running code reads it. The pipeline migrates before the new code starts.
- **Env changes are still a hand step** (the A–F blocks), before the release that needs them: the pipeline never edits env files.
- **A release that changes `infrastructure/oracle/quadlet/`** is refused by the pipeline and deployed by hand.

## Security

- **The deploy key can run only `deploy.sh`:** its line in `~/.ssh/authorized_keys` is `restrict,command="…/deploy.sh"`. With it, nobody gets a shell, a port forward or the env files, and the script deploys only commits already on `main`.
- **GitHub knows each VM's host key** (`DEPLOY_KNOWN_HOSTS`), so it never talks to an impostor.
- **This repository is public:** the workflow runs only for pushes to `main` and manual runs, never for pull requests. Secrets live in the `staging` and `production` environments.
- **Database URLs for migrations** are environment secrets in GitHub. Migrations run on GitHub's runners, never on the VMs.

## One-time setup

Each part below is done once. The command blocks follow the A–F format of the Oracle handoffs.

### 1. GitHub

In the repository's **Settings → Environments**, create:

- **`staging`**, with these environment secrets:
  - `DEPLOY_HOST`: the staging VM's public IP;
  - `DEPLOY_SSH_KEY`: the contents of `.local/deploy-keys/staging` (the private key, on the laptop);
  - `DEPLOY_KNOWN_HOSTS`: the contents of `.local/deploy-keys/staging.known_hosts`;
  - `DATABASE_URL`: staging's Neon branch, in the same form as `DATABASE_URL` in `backend/.env`.
- **`production`**, with the same four secrets for production (`.local/deploy-keys/production`, `production.known_hosts`, production's `DATABASE_URL`). Under **Deployment protection rules**, add yourself as a **required reviewer**, and limit deployment branches to `main`.

Then add a repository **variable** `DEPLOY_STAGING` = `true` (Settings → Secrets and variables → Actions → Variables), **only once staging is set up**.

### 2. The production VM (`130.61.104.247`)

After this pipeline's PR is merged:

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
uname -m && command -v flock && ls -l /etc/audoryn/
sudo sshd -T | grep -E '^(passwordauthentication|pubkeyauthentication|port) '
```

- **Expected:**
  - branch `main` and a clean tree;
  - `flock` found;
  - `passwordauthentication no` and `pubkeyauthentication yes`.
- In the Oracle console, the VM's security list or NSG must allow **TCP 22 from 0.0.0.0/0**: GitHub's runners connect from many addresses. With password login off and the deploy key restricted, that is the standard setup. If 22 is limited to your own IP today, tell me before changing it.

**B. Fast-forward** (the Phase 5 block B)

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend packages services infrastructure; fi
```

**D. This VM's identity for the pipeline** (no secrets)

```bash
sudo python3 - <<'PY'
from pathlib import Path
p = Path('/etc/audoryn/deploy.env')
if p.exists():
    raise SystemExit('STOP: /etc/audoryn/deploy.env already exists; no changes made')
p.write_text('# This VM in the deploy pipeline (docs/deploy/pipeline.md). No secrets.\n'
             'DEPLOY_ENV=production\nAUDORYN_PUBLIC_URL=https://130.61.104.247\n')
p.chmod(0o644)
print('Created', p)
PY
```

**E. The deploy key, restricted to the deploy script** (the public key from `.local/deploy-keys/production.pub`)

```bash
KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPgRYIq2tWfsiDVB8fyfXlkM8gnCRZJPagciBPsiin97 github-deploy-production' && if grep -qF "$KEY" ~/.ssh/authorized_keys; then echo 'STOP: the deploy key is already there'; else cp -a ~/.ssh/authorized_keys "$HOME/.ssh/authorized_keys.backup-deploy-$(date +%Y%m%d-%H%M%S)" && printf 'restrict,command="/opt/audoryn/AgentGate/infrastructure/oracle/deploy.sh" %s\n' "$KEY" >> ~/.ssh/authorized_keys && echo 'Deploy key added'; fi
bash /opt/audoryn/AgentGate/infrastructure/oracle/deploy.sh status
```

- **Expected:** `Deploy key added`, then `production runs <the commit B pulled>`.

**F. From the laptop:** the key may run only the script.

```bash
ssh -o IdentitiesOnly=yes -i .local/deploy-keys/production opc@130.61.104.247 status
ssh -o IdentitiesOnly=yes -i .local/deploy-keys/production opc@130.61.104.247 'cat /etc/audoryn/audoryn.env'
```

- **Expected:**
  - the first prints the same status as E;
  - the second is refused with `STOP: commands: deploy <commit>, rollback, status` (the env file is never shown).

### 3. The staging VM

A new Oracle VM (decided 2026-10-24). It is set up by hand once, like production was:

1. **In the Oracle console:**
   - create the instance with Oracle Linux 9, a public IP, and ingress for TCP 22, 80 and 443;
   - shape: `VM.Standard.A1.Flex` with at least 1 OCPU and 6 GB, within the Always Free Ampere allowance. Images are built on the VM itself, so ARM needs nothing else.
2. **Staging's own data:**
   - a **Neon branch** of production's database, for staging;
   - a **separate Upstash Redis** database;
   - QStash, the AI provider and object storage can be shared, with staging's own URLs.
3. **On the VM:** podman, nginx with TLS, the checkout at `/opt/audoryn/AgentGate`, the env files (production's, with staging's database, Redis, addresses and its own generated keys), the images, and the Quadlet services, as in Phase 5. Then `deploy.env` with `DEPLOY_ENV=staging` and its own deploy key, as in 2.D and 2.E.

The commands for 3 are written once production's layout has been read (nginx's site and TLS, and the env key names), so staging copies production exactly: see the handoff that follows this document.

### 4. Vercel (the web app)

- **Settings → Git → Production Branch:** `production`, so merges to `main` no longer go live directly.
- **Environment variables:** keep production's values for **Production**. For **Preview**, on the branch `main`, set `VITE_API_BASE_URL`, `VITE_AUTH_BASE_URL` and `VITE_REALTIME_URL` to the staging API.
- Add the `main` branch's preview address to staging's `CORS_ALLOWED_ORIGINS`.

### 5. First run

1. Set `DEPLOY_STAGING` = `true`. The next merge to `main` deploys to staging; follow it under **Actions → Deploy**.
2. Try the change on the staging web app.
3. **Actions → Deploy → Run workflow:** environment `production`, commit empty, then approve. The VM's log shows the switch and the checks. The `production` branch moves, and Vercel deploys the web app.

## When the pipeline stops

Every stop prints why, in the workflow's log, with `STOP:`.

| Message | Meaning | What to do |
|---|---|---|
| `the <image> image did not build; nothing was restarted` | A build failed on the VM | Fix it in a PR; the VM still runs the previous version |
| `<commit> was rolled back; <env> runs <previous> again` | The new version failed a check | Read the check lines above it; fix in a PR |
| `the rollback to <previous> failed too` | Neither version passes its checks | Open the shell: `bash …/deploy.sh status`, `check_services.py`, the logs |
| `this release changes the service layout` | `quadlet/` changed | Deploy it by hand with the handoff's blocks |
| `another deploy is running on this VM` | Two runs overlapped | Wait; the workflow queues per environment |
