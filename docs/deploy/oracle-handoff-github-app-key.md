# Oracle handoff: GitHub App key and quoted GitHub settings

Written 14 October 2026, for PR #124 (`fix/github-app-key`, merged as `cc3fd62`). It follows
*Audoryn Oracle Update and Deployment Rules — Illustrated Edition*: read-only checks first,
backed-up edits of named keys only, the application parser before any restart, and nothing changed
without approval.

**Target:**
- **AgentGate Oracle:** `130.61.104.247`, repository `/opt/audoryn/AgentGate`, service
  `audoryn-api.service`, env file `/etc/audoryn/audoryn.env`, image `localhost/audoryn-api:latest`.
- **Not touched:** Console, Vercel, QStash, the database.

## 1. What is known

| | Finding | Evidence |
|---|---|---|
| Confirmed | Before PR #124, Oracle could not read `GITHUB_APP_PRIVATE_KEY` | `app_jwt()` failed: `ValueError … MalformedFraming` |
| Confirmed | Oracle sends GitHub a client ID with literal quotes | The "Reconnect GitHub" URL has `client_id=%27Iv23liuOHgZtkE2bGuu1%27`, and GitHub shows 404 |
| Plausible | `GITHUB_APP_ID` also has literal quotes, so token renewals are signed for app `'5211774'` and GitHub rejects them ("The provider rejected credential renewal. Reconnect this account.") | Same source file and pattern; check A shows it |
| Unknown | Whether Oracle's image already contains PR #124 | Check A shows it |

**Cause:** podman's `--env-file` keeps surrounding quotes as part of the value and has no
multi-line values. The laptop's dotenv loader removes quotes and accepts multi-line values.

## 2. What PR #124 changed (code)

- **The key:**
  - `GITHUB_APP_PRIVATE_KEY` is read from the one-line form (`\n` where the line breaks were),
    with or without surrounding quotes;
  - multi-line keys still work;
  - an unreadable key now gives a clear message instead of an internal error: "The GitHub App
    private key on this server could not be read. Check GITHUB_APP_PRIVATE_KEY."
- **Logging:** interrupted runs log the file, line and function they failed at
  (`Runtime interrupted … at=…`), never the error message.
- **No migration, no new dependencies, no new settings.** The backend changed, so the image needs
  rebuilding.

PR #124 does **not** remove quotes from other settings, so the quoted `GITHUB_CLIENT_ID` (and
possibly `GITHUB_APP_ID`) needs the targeted env edit in step C.

## 3. Steps (each one only after approval)

### A. Read-only check

This prints configured/quoted flags only, never values.

```bash
cd /opt/audoryn/AgentGate || exit 1
printf 'Repo commit: '; git rev-parse --short HEAD
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
podman run --rm -i --env-file /etc/audoryn/audoryn.env localhost/audoryn-api:latest python - <<'PY'
from app.bootstrap.settings import settings
import app.execution.providers.native.github.authentication as github_auth

def flags(name):
    raw = getattr(settings, name)
    value = raw.get_secret_value() if hasattr(raw, "get_secret_value") else (raw or "")
    quoted = len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"')
    print(f"{name}: configured={bool(value)} wrapping_quotes={quoted}")

for name in ("github_app_id", "github_client_id", "github_app_slug", "github_callback_url",
             "github_login_callback_url", "github_frontend_url", "coding_template_id",
             "coding_allowed_hosts", "github_client_secret", "github_webhook_secret",
             "github_app_private_key", "integration_encryption_key", "openrouter_api_key"):
    flags(name)
print("image has PR #124 key fix:", hasattr(github_auth, "private_key_pem"))
try:
    github_auth.app_jwt()
    print("github_app_key_signing: OK")
except Exception as error:
    print("github_app_key_signing: FAIL", type(error).__name__)
PY
```

**Reading the result:**
- **`image has PR #124 key fix: False`:** do step B before anything else.
- **`wrapping_quotes=True`:** fix each simple setting flagged this way in step C. Leave
  `github_app_private_key` alone; the new code handles its quotes. Never edit
  `integration_encryption_key` without a separate check (rules section 14).
- **`github_app_key_signing: FAIL` with the new image:** the key has to be put on one line (step D).

### B. Pull and rebuild for PR #124 (if the image doesn't have it)

1. **Pull:** follow rules section 3 / section 12, checkpoints A and B (`git pull --ff-only origin main`), and
   confirm the result is `cc3fd62` or later.
2. **Rebuild:** the backend changed, so rebuild following rules section 16:

```bash
cd /opt/audoryn/AgentGate || exit 1
podman build -f backend/Containerfile -t localhost/audoryn-api:latest . || exit 1
podman image inspect localhost/audoryn-api:latest --format '{{.Id}} {{.Created}}'
```

The rebuild does not restart the API. Run check A again: it should now show `True` for the key
fix.

### C. Remove the wrapping quotes from the named keys only

**Before running:**
- Put only the env names that check A flagged into `NAMES`. Settings map to env names in
  uppercase, for example `github_client_id` → `GITHUB_CLIENT_ID`.
- **Do not** include `GITHUB_APP_PRIVATE_KEY`, `INTEGRATION_ENCRYPTION_KEY` or any JSON value.

**What the script does:**
- stops if a key is missing or appears more than once;
- makes a backup;
- keeps the owner, permissions and every other line unchanged;
- replaces the file atomically.

```bash
sudo python3 - <<'PY'
from pathlib import Path
from datetime import datetime
import os, shutil, tempfile

NAMES = ["GITHUB_CLIENT_ID", "GITHUB_APP_ID"]  # only the keys check A flagged
p = Path('/etc/audoryn/audoryn.env')
if not p.is_file():
    raise SystemExit('STOP: env file missing')
old = p.read_text()
lines = old.splitlines(keepends=True)
changed = []
for name in NAMES:
    if name in ('GITHUB_APP_PRIVATE_KEY', 'INTEGRATION_ENCRYPTION_KEY'):
        raise SystemExit(f'STOP: {name} must not be edited by this script')
    found = [i for i, line in enumerate(lines)
             if line.strip() and not line.lstrip().startswith('#')
             and '=' in line and line.split('=', 1)[0].strip() == name]
    if len(found) != 1:
        raise SystemExit(f'STOP: {name} found {len(found)} times; no changes made')
    i = found[0]
    body = lines[i].rstrip('\r\n')
    ending = lines[i][len(body):]
    key, value = body.split('=', 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        lines[i] = f'{key}={value[1:-1]}{ending}'
        changed.append(name)
if not changed:
    print('No change: those keys have no wrapping quotes')
    raise SystemExit(0)
backup = p.with_name(p.name + '.backup-github-quotes-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
shutil.copy2(p, backup)
fd, temp = tempfile.mkstemp(prefix='.audoryn.env.', dir=p.parent)
try:
    with os.fdopen(fd, 'w') as f:
        f.write(''.join(lines))
        f.flush()
        os.fsync(f.fileno())
    st = p.stat()
    os.chown(temp, st.st_uid, st.st_gid)
    os.chmod(temp, st.st_mode & 0o7777)
    os.replace(temp, p)
except Exception:
    if os.path.exists(temp):
        os.unlink(temp)
    raise
print('Backup:', backup)
print('Removed wrapping quotes from:', ', '.join(changed))
print('Other keys and credentials preserved')
PY
```

Run check A again. Every key you changed must now show `wrapping_quotes=False`. If anything
looks wrong, **do not restart**: restore the printed backup with `sudo cp -a BACKUP
/etc/audoryn/audoryn.env`, after approval.

### D. Only if the key still can't be read with the new image

1. **Make the one-line form.** With the `.pem` file from the GitHub App settings, this prints it
   for `GITHUB_APP_PRIVATE_KEY`:

```bash
awk 'NF {sub(/\r/, ""); printf "%s\\n", $0}' audoryn.private-key.pem; echo
```

2. **Write it into the env file** with a step C-style targeted, backed-up edit of that one key.
   Don't paste it into chat.
3. **No `.pem` file:** generate a new private key in the GitHub App settings. The old key keeps
   working until you delete it.

### E. Controlled restart (rules section 7 / section 17)

```bash
systemctl --user restart audoryn-api.service
sleep 20
systemctl --user is-active audoryn-api.service
curl -sS -i --max-time 20 http://127.0.0.1:8000/health/ready
curl -sS -i --max-time 20 https://130.61.104.247/health/ready
IMAGE_ID="$(podman image inspect localhost/audoryn-api:latest --format '{{.Id}}')"
RUNNING_ID="$(podman inspect --type container audoryn-api --format '{{.Image}}')"
[ "$IMAGE_ID" = "$RUNNING_ID" ] && echo 'PASS: running the new image' || echo 'FAIL: different image running'
```

Then run the check from A against the **running** container: replace the `podman run --rm -i
--env-file … localhost/audoryn-api:latest` line with `podman exec -i audoryn-api`. It should show
no `wrapping_quotes=True` on the edited keys and `github_app_key_signing: OK`.

### F. Reconnect and test end to end

1. **GitHub App settings:** check that the callback URLs point at Oracle (rules section 20 table). This
   wasn't confirmed earlier.
2. **Reconnect:** in AgentGate, open Connections → DIVINEJMG → Settings and press **Reconnect
   GitHub**.
   - The GitHub page should now open (no `%27` in the address).
   - After approving, the account returns to Healthy and the reauthorise warning goes away.
3. **Task:** press **Resume** on the waiting task, or ask "What are the open issues in
   DIVINEJMG/school?".
4. **Proof:** the result itself, with the issue list posted in the conversation. A `202` from
   `runtime/execute` only means the request was accepted.

## 4. Rolling back

- **Env:** restore the backup that step C printed (after approval), then restart.
- **Code:** rebuild from `18ba124`. With that image the one-line key form isn't understood, so the
  key would need real line breaks again.

## 5. Proof (PR #124)

- **Backend:** 1,539 tests pass with and without the laptop `.env`, including
  `test_github_app_key.py`, which reads a real key in six forms.
- **Old versus new code:** the old code fails on the one-line form and the new code reads it.
- **Static checks:** ruff, pyright (0 errors) and the CI verify scripts pass.
