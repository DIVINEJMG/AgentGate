# Oracle commands: commercial plans, payments, Plans page, upgrade prompts, What's new

For `docs/deploy/oracle-handoff-commercial-plans.md`, in the same form as the earlier handoffs
(blocks A-G, rollback tag, image check) and `Audoryn_Oracle_Operations_Rules_2026-10-14.md`. Run
each block only after the previous one is checked; stop at any `STOP` or `FAIL`. The blocks do not
use `exit`, so a failed command does not close the SSH session. Nothing prints a secret.

| | AgentGate | Console |
|---|---|---|
| VM | `130.61.104.247` | `130.61.172.186` |
| Repo | `/opt/audoryn/AgentGate` | `/opt/audoryn-console/Audoryn-Console` |
| Service / container | `audoryn-api.service` / `audoryn-api` | `audoryn-console-api.service` / `audoryn-console-api` |
| Env | `/etc/audoryn/audoryn.env` | `/etc/audoryn-console/audoryn-console.env` |

## 0. Before Oracle

1. **GitHub:** merge both pull requests into `main`; note both merge SHAs.
2. **Laptop, production migrations (approval):** the laptop `backend\.env` already points at the
   production database (`ep-long-glitter`). PowerShell:

   ```powershell
   cd C:\Users\divin\AgentGate\backend
   .venv\Scripts\python.exe -m alembic current
   .venv\Scripts\python.exe -m alembic upgrade head
   .venv\Scripts\python.exe -m alembic current
   ```

   Expected: `0019_usage_prompt_chars`, then `0022_user_content_state (head)`. The Console's
   `0009`-`0011` are already applied.

## 1. Settings to add (from the laptop `.env` files)

Same key pairs as the laptop (as the handoff says). Values marked **laptop** are copied from the
laptop file named; the others are Oracle's own addresses. Payment keys are **not** copied: the
production Console refuses test keys, so payments stay off until the live keys.

**AgentGate** (`/etc/audoryn/audoryn.env`, from `AgentGate\backend\.env`):

| Key | Value |
|---|---|
| `CONSOLE_READS_ENABLED` | `true` |
| `CONSOLE_READS_AUDIENCE`, `CONSOLE_READS_KEY_ID`, `CONSOLE_READS_KEY_SECRET`, `CONSOLE_READS_KEY_SCOPES` | laptop |
| `CONSOLE_ENTITLEMENTS_ENABLED` | `true` |
| `CONSOLE_ENTITLEMENTS_KEY_ID`, `CONSOLE_ENTITLEMENTS_KEY_SECRET` | laptop |
| `CONSOLE_BILLING_ENABLED` | `true` |
| `CONSOLE_BILLING_ORIGIN` | `https://130.61.172.186` (the Console API; the laptop has `localhost:8001`, not that) |
| `CONSOLE_BILLING_KEY_ID`, `CONSOLE_BILLING_KEY_SECRET` | laptop |
| `ENTITLEMENTS_ENFORCEMENT` | **not now** (block H) |

**Console** (`/etc/audoryn-console/audoryn-console.env`, from `Audoryn-Console\backend\.env`):

| Key | Value |
|---|---|
| `AGENTGATE_READS_ENABLED` | `true` |
| `AGENTGATE_INTERNAL_ORIGIN` | `https://130.61.104.247` |
| `AGENTGATE_READS_AUDIENCE`, `AGENTGATE_READS_KEY_ID`, `AGENTGATE_READS_KEY_SECRET`, `AGENTGATE_READS_KEY_SCOPES` | laptop |
| `AGENTGATE_ENTITLEMENTS_ENABLED` | `true` |
| `AGENTGATE_ENTITLEMENTS_ORIGIN` | `https://130.61.104.247` |
| `AGENTGATE_ENTITLEMENTS_KEY_ID`, `AGENTGATE_ENTITLEMENTS_KEY_SECRET` | laptop |
| `AGENTGATE_BILLING_ENABLED` | `true` |
| `AGENTGATE_BILLING_KEY_ID`, `AGENTGATE_BILLING_KEY_SECRET` | laptop |
| `AGENTGATE_WEB_ORIGIN` | `https://audoryn.vercel.app` |

---

## 2. AgentGate VM (`130.61.104.247`)

**A. Read-only inspection**

```bash
cd /opt/audoryn/AgentGate && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-api.service
df -h / /var/tmp
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'
for k in CONSOLE_READS_ENABLED CONSOLE_ENTITLEMENTS_ENABLED CONSOLE_BILLING_ENABLED ENTITLEMENTS_ENFORCEMENT; do printf '%s: %s line(s)\n' "$k" "$(sudo grep -cE "^\s*$k=" /etc/audoryn/audoryn.env)"; done
```

- **Expected:** branch `main`, a clean tree, `Current` starting `fdf4402`, `Remote` = the merge
  SHA, 4 GB or more free, each key `0 line(s)`.

**B. Fast-forward**

```bash
cd /opt/audoryn/AgentGate && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend/Containerfile backend/pyproject.toml backend/migrations; fi
```

- **Expected:** `After` = the merge SHA. Files listed: the migrations `0020`, `0021`, `0022`
  (already applied in step 0); no `Containerfile`.

**C. Tag the rollback image, then rebuild**

```bash
cd /opt/audoryn/AgentGate && podman tag localhost/audoryn-api:latest localhost/audoryn-api:rollback-fdf4402 && podman build -f backend/Containerfile -t localhost/audoryn-api:latest . && podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

**D. Add the settings**

Everything is filled in except three secrets. Replace each `PASTE_n` with the value after `=` on
that line of your **laptop** file `C:\Users\divin\AgentGate\backend\.env`:

| In the script | Copy the value of this laptop line |
|---|---|
| `PASTE_1` | `CONSOLE_READS_KEY_SECRET=` |
| `PASTE_2` | `CONSOLE_ENTITLEMENTS_KEY_SECRET=` |
| `PASTE_3` | `CONSOLE_BILLING_KEY_SECRET=` |

Keep the quotes around each value in the script; paste only the value (no `=`, no spaces). The
script stops without changing anything if a `PASTE` is left or a key already exists. Otherwise it
backs the file up (and prints the backup path), adds the 12 lines at the end, and keeps every other
line, the owner and the permissions.

```bash
sudo python3 - <<'PY'
from pathlib import Path
from datetime import datetime
import os, shutil, tempfile

VALUES = {
    "CONSOLE_READS_ENABLED": "true",
    "CONSOLE_READS_AUDIENCE": "agentgate",
    "CONSOLE_READS_KEY_ID": "console-reads-2026-10",
    "CONSOLE_READS_KEY_SECRET": "PASTE_1",
    "CONSOLE_READS_KEY_SCOPES": "visibility.read,observability.read,security.read,notifications.read",
    "CONSOLE_ENTITLEMENTS_ENABLED": "true",
    "CONSOLE_ENTITLEMENTS_KEY_ID": "console-entitlements-2026-10",
    "CONSOLE_ENTITLEMENTS_KEY_SECRET": "PASTE_2",
    "CONSOLE_BILLING_ENABLED": "true",
    "CONSOLE_BILLING_ORIGIN": "https://130.61.172.186",
    "CONSOLE_BILLING_KEY_ID": "agentgate-billing-2026-10",
    "CONSOLE_BILLING_KEY_SECRET": "PASTE_3",
}
p = Path('/etc/audoryn/audoryn.env')
if not p.is_file():
    raise SystemExit('STOP: env file missing')
for name, value in VALUES.items():
    if 'PASTE' in value or not value or value[0] in '"\'' or ' ' in value:
        raise SystemExit(f'STOP: {name} has no proper value yet; no changes made')
lines = p.read_text().splitlines(keepends=True)
for name in VALUES:
    found = [l for l in lines if l.strip() and not l.lstrip().startswith('#') and '=' in l and l.split('=', 1)[0].strip() == name]
    if found:
        raise SystemExit(f'STOP: {name} already present ({len(found)} times); no changes made')
if lines and not lines[-1].endswith('\n'):
    lines[-1] += '\n'
lines += ['# Commercial plans: Console channels, added ' + datetime.now().strftime('%Y-%m-%d') + '\n']
lines += [f'{name}={value}\n' for name, value in VALUES.items()]
backup = p.with_name(p.name + '.backup-commercial-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
shutil.copy2(p, backup)
fd, temp = tempfile.mkstemp(prefix='.audoryn.env.', dir=p.parent)
try:
    with os.fdopen(fd, 'w') as f:
        f.write(''.join(lines)); f.flush(); os.fsync(f.fileno())
    st = p.stat()
    os.chown(temp, st.st_uid, st.st_gid); os.chmod(temp, st.st_mode & 0o7777); os.replace(temp, p)
except Exception:
    if os.path.exists(temp):
        os.unlink(temp)
    raise
print('Backup:', backup)
print('Added:', ', '.join(VALUES))
print('Other keys and credentials preserved')
PY
```

- **Expected:** `Backup: /etc/audoryn/audoryn.env.backup-commercial-...` and the 12 key names.

**E. Read-only checks with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python - <<'PY'
from app.bootstrap.settings import settings
from app.application.console_billing import load_channel
print("environment:", settings.environment)
print("reads:", settings.console_reads_enabled, settings.console_reads_key_id)
print("entitlements:", settings.console_entitlements_enabled, settings.console_entitlements_key_id)
print("billing:", settings.console_billing_origin, "| channel valid:", load_channel(settings) is not None)
print("enforcement:", settings.entitlements_enforcement)
PY
curl -sS -o /dev/null -w 'console api over https: %{http_code}\n' --max-time 15 https://130.61.172.186/health/ready
```

- **Expected:** `0022_user_content_state (head)`; all enabled; `channel valid: True`;
  `enforcement: False`; `console api over https: 200` (a certificate error means AgentGate cannot
  reach the Console: stop and send me the output).

**F. Restart and verify**

```bash
systemctl --user restart audoryn-api.service
sleep 20
systemctl --user is-active audoryn-api.service
curl -sS --max-time 20 http://127.0.0.1:8000/health/ready; echo
curl -sS --max-time 20 https://130.61.104.247/health/ready; echo
[ "$(podman image inspect localhost/audoryn-api:latest --format '{{.Id}}')" = "$(podman inspect --type container audoryn-api --format '{{.Image}}')" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
podman logs --since 5m audoryn-api 2>&1 | grep -E "Traceback|ERROR|startup complete"
```

---

## 3. Console VM (`130.61.172.186`)

**A. Read-only inspection**

```bash
cd /opt/audoryn-console/Audoryn-Console && git branch --show-current && git status --short && git fetch origin main && printf 'Current: %s\nRemote:  %s\n' "$(git rev-parse HEAD)" "$(git rev-parse origin/main)"
systemctl --user is-active audoryn-console-api.service
df -h / /var/tmp
podman images --format 'table {{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}'
for k in AGENTGATE_READS_ENABLED AGENTGATE_INTERNAL_ORIGIN AGENTGATE_ENTITLEMENTS_ENABLED AGENTGATE_BILLING_ENABLED AGENTGATE_WEB_ORIGIN PAYSTACK_SECRET_KEY PADDLE_API_KEY; do printf '%s: %s line(s)\n' "$k" "$(sudo grep -cE "^\s*$k=" /etc/audoryn-console/audoryn-console.env)"; done
curl -sS -o /dev/null -w 'agentgate over https: %{http_code}\n' --max-time 15 https://130.61.104.247/health/ready
```

- **Expected:** branch `main`, a clean tree, each key `0 line(s)` (if `AGENTGATE_INTERNAL_ORIGIN`
  or a reads key already exists, send me the output first), `agentgate over https: 200`.

**B. Fast-forward**

```bash
cd /opt/audoryn-console/Audoryn-Console && if [ "$(git branch --show-current)" != main ] || [ -n "$(git status --porcelain)" ]; then echo 'STOP: wrong branch or dirty working tree'; else OLD="$(git rev-parse HEAD)" && echo "$OLD" > "$HOME/console-rollback.sha" && git pull --ff-only origin main && printf 'Before: %s\nAfter:  %s\n' "$OLD" "$(git rev-parse HEAD)" && git -c core.pager=cat diff --name-only "$OLD" HEAD -- backend/Containerfile backend/pyproject.toml backend/migrations; fi
```

**C. Tag the rollback image, then rebuild**

```bash
cd /opt/audoryn-console/Audoryn-Console && podman tag localhost/audoryn-console-api:latest "localhost/audoryn-console-api:rollback-$(cut -c1-7 "$HOME/console-rollback.sha")" && podman build -f backend/Containerfile -t localhost/audoryn-console-api:latest . && podman image inspect localhost/audoryn-console-api:latest --format '{{.Id}} {{.Created}}'
```

**D. Add the settings**

Everything is filled in except three secrets. Replace each `PASTE_n` with the value after `=` on
that line of your **laptop** file `C:\Users\divin\Audoryn-Console\backend\.env`:

| In the script | Copy the value of this laptop line |
|---|---|
| `PASTE_1` | `AGENTGATE_READS_KEY_SECRET=` |
| `PASTE_2` | `AGENTGATE_ENTITLEMENTS_KEY_SECRET=` |
| `PASTE_3` | `AGENTGATE_BILLING_KEY_SECRET=` |

These are the same three secrets as AgentGate's, taken from the Console's own file. The addresses
are already in the script: do **not** copy `AGENTGATE_INTERNAL_ORIGIN`,
`AGENTGATE_ENTITLEMENTS_ORIGIN` or `AGENTGATE_WEB_ORIGIN` from the laptop (they are `localhost`
there). Payment keys are not added.

```bash
sudo python3 - <<'PY'
from pathlib import Path
from datetime import datetime
import os, shutil, tempfile

VALUES = {
    "AGENTGATE_READS_ENABLED": "true",
    "AGENTGATE_INTERNAL_ORIGIN": "https://130.61.104.247",
    "AGENTGATE_READS_AUDIENCE": "agentgate",
    "AGENTGATE_READS_KEY_ID": "console-reads-2026-10",
    "AGENTGATE_READS_KEY_SECRET": "PASTE_1",
    "AGENTGATE_READS_KEY_SCOPES": "visibility.read,observability.read,security.read,notifications.read",
    "AGENTGATE_ENTITLEMENTS_ENABLED": "true",
    "AGENTGATE_ENTITLEMENTS_ORIGIN": "https://130.61.104.247",
    "AGENTGATE_ENTITLEMENTS_KEY_ID": "console-entitlements-2026-10",
    "AGENTGATE_ENTITLEMENTS_KEY_SECRET": "PASTE_2",
    "AGENTGATE_BILLING_ENABLED": "true",
    "AGENTGATE_BILLING_KEY_ID": "agentgate-billing-2026-10",
    "AGENTGATE_BILLING_KEY_SECRET": "PASTE_3",
    "AGENTGATE_WEB_ORIGIN": "https://audoryn.vercel.app",
}
p = Path('/etc/audoryn-console/audoryn-console.env')
if not p.is_file():
    raise SystemExit('STOP: env file missing')
for name, value in VALUES.items():
    if 'PASTE' in value or not value or value[0] in '"\'' or ' ' in value:
        raise SystemExit(f'STOP: {name} has no proper value yet; no changes made')
lines = p.read_text().splitlines(keepends=True)
for name in VALUES:
    found = [l for l in lines if l.strip() and not l.lstrip().startswith('#') and '=' in l and l.split('=', 1)[0].strip() == name]
    if found:
        raise SystemExit(f'STOP: {name} already present ({len(found)} times); no changes made')
if lines and not lines[-1].endswith('\n'):
    lines[-1] += '\n'
lines += ['# Commercial plans: AgentGate channels, added ' + datetime.now().strftime('%Y-%m-%d') + '\n']
lines += [f'{name}={value}\n' for name, value in VALUES.items()]
backup = p.with_name(p.name + '.backup-commercial-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
shutil.copy2(p, backup)
fd, temp = tempfile.mkstemp(prefix='.audoryn-console.env.', dir=p.parent)
try:
    with os.fdopen(fd, 'w') as f:
        f.write(''.join(lines)); f.flush(); os.fsync(f.fileno())
    st = p.stat()
    os.chown(temp, st.st_uid, st.st_gid); os.chmod(temp, st.st_mode & 0o7777); os.replace(temp, p)
except Exception:
    if os.path.exists(temp):
        os.unlink(temp)
    raise
print('Backup:', backup)
print('Added:', ', '.join(VALUES))
print('Other keys and credentials preserved')
PY
```

- **Expected:** `Backup: /etc/audoryn-console/audoryn-console.env.backup-commercial-...` and the
  14 key names.

**E. Read-only checks with the new image**

```bash
podman run --rm -i --env-file /etc/audoryn-console/audoryn-console.env localhost/audoryn-console-api:latest alembic current
podman run --rm -i --env-file /etc/audoryn-console/audoryn-console.env localhost/audoryn-console-api:latest python - <<'PY'
from app.bootstrap.settings import get_settings
settings = get_settings()
print("environment:", settings.environment)
print("reads:", settings.agentgate_reads_enabled, settings.agentgate_internal_origin)
print("entitlements:", settings.agentgate_entitlements_enabled, settings.agentgate_entitlements_origin)
print("billing:", settings.agentgate_billing_enabled, "| web:", settings.agentgate_web_origin)
print("payments configured:", bool(settings.paystack_secret_key), bool(settings.paddle_api_key))
PY
```

- **Expected:** `0011_plan_pages (head)`; all enabled; `payments configured: False False`.
  An error naming a setting means the settings are not accepted: do not restart.

**F. Restart and verify**

```bash
systemctl --user restart audoryn-console-api.service
sleep 20
systemctl --user is-active audoryn-console-api.service
curl -sS --max-time 20 http://127.0.0.1:8001/health/ready; echo
[ "$(podman image inspect localhost/audoryn-console-api:latest --format '{{.Id}}')" = "$(podman inspect --type container audoryn-console-api --format '{{.Image}}')" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
podman logs --since 5m audoryn-console-api 2>&1 | grep -E "Traceback|ERROR|startup complete|Entitlement"
```

**G. Give every workspace its plan (writes Console deliveries; approval)**

```bash
podman exec audoryn-console-api python -m app.bootstrap.entitlement_delivery
sleep 20
```

- **Expected:** `organisations staged: N`; the worker sends them within seconds.

---

## 4. AgentGate VM: every workspace has a plan, then enforcement

**Check (read-only):**

```bash
podman exec -i audoryn-api python - <<'PY'
import asyncio
from sqlalchemy import func, select
from app.infrastructure.database.models import Organization, OrganizationEntitlement
from app.infrastructure.database.session import session_factory
async def main():
    async with session_factory() as session:
        orgs = await session.scalar(select(func.count()).select_from(Organization))
        plans = await session.scalar(select(func.count()).select_from(OrganizationEntitlement))
        print("organisations:", orgs, "| with a plan:", plans, "| PASS" if orgs == plans else "| NOT YET")
asyncio.run(main())
PY
```

**H. Enforcement (only after PASS):** run AgentGate's block D again with its whole `VALUES = {...}`
replaced by `VALUES = {"ENTITLEMENTS_ENFORCEMENT": "true"}` (nothing to paste), then block F.

## 5. Vercel (separate approval; auto-deploy is off)

- Deploy project `audoryn` at the AgentGate merge SHA; deploy `audoryn-console` at the Console
  merge SHA. No new Vercel variables. Wait for `READY`; check each alias serves that SHA.

## 6. Rollback (only with your confirmation)

- **Env:** `sudo cp -a <Backup printed by block D> <env file>`, then block F.
- **Image:** `podman tag localhost/audoryn-api:rollback-fdf4402 localhost/audoryn-api:latest` (Console:
  its `rollback-<sha>` tag), then block F. The old code runs with the new tables.
- **Enforcement only:** remove the `ENTITLEMENTS_ENFORCEMENT` line (restore block H's backup), then block F.
