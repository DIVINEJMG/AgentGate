#!/usr/bin/env bash
# Deploys AgentGate on this VM (docs/deploy/pipeline.md). It is the only command GitHub's deploy key
# may run: that key's line in ~/.ssh/authorized_keys forces it. The command comes from
# SSH_ORIGINAL_COMMAND, or from the arguments when run by hand:
#
#   deploy <commit>   build the images the release changes, switch to them, check; roll back on failure
#   rollback          return to the commit deployed before this one
#   status            this VM's environment and its deployed commits, newest last
#
# It refuses a commit that is not on origin/main, and a release that changes the service layout
# (infrastructure/oracle/quadlet/): that stays a hand deploy. Migrations are not run here: the
# pipeline runs them from GitHub before calling this. Images are built before anything restarts,
# so a failed build changes nothing that runs.
set -euo pipefail

# Run from a copy: a deploy checks out another commit, which may change this file under bash.
if [ -z "${AUDORYN_DEPLOY_COPY:-}" ]; then
  copy="$(mktemp)"
  cp "$0" "$copy"
  AUDORYN_DEPLOY_COPY=1 exec bash "$copy" "$@"
fi
trap 'rm -f "$0"' EXIT

REPO="${AUDORYN_REPO:-/opt/audoryn/AgentGate}"  # overridable for the tests only
HISTORY="$HOME/.audoryn-deploys"  # one deployed commit per line, newest last
# DEPLOY_ENV (staging or production) and AUDORYN_PUBLIC_URL: this VM's identity, no secrets.
# shellcheck source=/dev/null
. "${AUDORYN_DEPLOY_CONFIG:-/etc/audoryn/deploy.env}"
export AUDORYN_PUBLIC_URL
# Restart order: steps first, then people's routes, then the entry point (a few seconds of 502).
ORDER="execution core gateway"

say() { printf '%s  %s\n' "$(date -u +%H:%M:%S)" "$*"; }
fail() { say "STOP: $*"; exit 1; }

exec 9>"$HOME/.audoryn-deploy.lock"
flock -n 9 || fail "another deploy is running on this VM"
read -r command commit extra <<<"${SSH_ORIGINAL_COMMAND:-$*}" || true
[ -z "${extra:-}" ] || fail "unexpected arguments"
cd "$REPO"

# The images a change between two commits needs rebuilt. Tests and docs never do.
changed_images() {
  local files
  files="$(git diff --name-only "$1" "$2" | grep -vE '(^|/)tests/|\.md$' || true)"
  local image
  for image in $ORDER; do
    case $image in
      core) grep -qE '^(backend|packages)/' <<<"$files" && echo core ;;
      execution) grep -qE '^(packages|services/execution)/' <<<"$files" && echo execution ;;
      gateway) grep -qE '^services/gateway/' <<<"$files" && echo gateway ;;
    esac
  done
  true
}

containerfile() {
  case $1 in
    core) echo backend/Containerfile.api ;;
    execution) echo services/execution/Containerfile ;;
    gateway) echo services/gateway/Containerfile ;;
  esac
}

restart() {
  case $1 in
    core) bash "$REPO/infrastructure/oracle/restart_core.sh" ;;  # one copy at a time, no downtime
    execution) systemctl --user restart audoryn-execution.service; answers http://127.0.0.1:8201/health/live ;;
    gateway) systemctl --user restart audoryn-gateway.service; answers http://127.0.0.1:8000/health/gateway ;;
  esac
}

answers() {
  local _
  for _ in $(seq 90); do curl -sf "$1" >/dev/null && return 0; sleep 2; done
  say "$1 did not answer within 3 minutes"
  return 1
}

check() {
  sleep 10
  python3 "$REPO/infrastructure/oracle/check_services.py"
}

# Checks out `target` and runs its images: each image in `images` gets its `:<commit>` tag (built if
# missing) as `:latest`, then restarts, in ORDER. The images now running were tagged `:<from>` first.
switch() {
  local from="$1" target="$2" images="$3" image
  for image in $images; do
    podman image exists "localhost/audoryn-$image:${from:0:12}" ||
      podman tag "localhost/audoryn-$image:latest" "localhost/audoryn-$image:${from:0:12}"
  done
  git checkout -q -B main "$target"
  for image in $images; do
    if ! podman image exists "localhost/audoryn-$image:${target:0:12}"; then
      say "building $image"
      if ! podman build -q -f "$(containerfile "$image")" -t "localhost/audoryn-$image:${target:0:12}" . >/dev/null; then
        git checkout -q -B main "$from"
        fail "the $image image did not build; nothing was restarted"
      fi
    fi
  done
  for image in $ORDER; do
    case " $images " in *" $image "*)
      podman tag "localhost/audoryn-$image:${target:0:12}" "localhost/audoryn-$image:latest"
      say "restarting $image"
      restart "$image" || return 1
    esac
  done
}

# Keeps the images of the last three deployed commits; older commit tags and untagged images go.
prune() {
  local keep tag
  keep="$(tail -n 3 "$HISTORY" | cut -c1-12)"
  for tag in $(podman images --format '{{.Repository}}:{{.Tag}}' | grep -E '^localhost/audoryn-(core|execution|gateway):[0-9a-f]{12}$'); do
    grep -qx "${tag##*:}" <<<"$keep" || podman rmi -f "$tag" >/dev/null || true
  done
  podman image prune -f >/dev/null || true
}

# Moves from the running commit to `target`; back to the running one if its checks fail.
release() {
  local target="$1" from images
  [ -z "$(git status --porcelain)" ] || fail "the checkout has local changes"
  from="$(git rev-parse HEAD)"
  [ -s "$HISTORY" ] || echo "$from" >"$HISTORY"
  if [ "$from" = "$target" ]; then check; say "deployed $target to $DEPLOY_ENV (already running)"; return; fi
  if git diff --name-only "$from" "$target" | grep -q '^infrastructure/oracle/quadlet/'; then
    fail "this release changes the service layout (infrastructure/oracle/quadlet/): deploy it by hand"
  fi
  images="$(changed_images "$from" "$target" | xargs)"
  say "$DEPLOY_ENV: ${from:0:12} -> ${target:0:12}; images: ${images:-none}"
  if switch "$from" "$target" "$images" && check; then
    echo "$target" >>"$HISTORY"
    prune
    say "deployed $target to $DEPLOY_ENV"  # the workflow reads this line
    return
  fi
  say "checks failed: rolling back to ${from:0:12}"
  if switch "$target" "$from" "$images" && check; then
    fail "${target:0:12} was rolled back; $DEPLOY_ENV runs ${from:0:12} again"
  fi
  fail "the rollback to ${from:0:12} failed too: $DEPLOY_ENV needs a hand (docs/deploy/pipeline.md)"
}

case "$command" in
  deploy)
    [[ "${commit:-}" =~ ^[0-9a-f]{40}$ ]] || fail "give the full 40-character commit"
    git fetch -q origin main
    git merge-base --is-ancestor "$commit" origin/main || fail "$commit is not on main"
    release "$commit"
    ;;
  rollback)
    [ -z "${commit:-}" ] || fail "rollback takes no commit"
    [ "$(wc -l <"$HISTORY" 2>/dev/null || echo 0)" -ge 2 ] || fail "no earlier deploy to return to"
    previous="$(tail -n 2 "$HISTORY" | head -n 1)"
    release "$previous"
    ;;
  status)
    say "$DEPLOY_ENV runs $(git rev-parse HEAD)"
    tail -n 5 "$HISTORY" 2>/dev/null || true
    ;;
  *) fail "commands: deploy <commit>, rollback, status" ;;
esac
