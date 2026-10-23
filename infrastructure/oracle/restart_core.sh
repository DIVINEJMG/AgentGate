#!/usr/bin/env bash
# Restarts the core copies one at a time, so people are always served (Phase 5, step 5). Run it after
# building a new localhost/audoryn-core image:
#
#     bash /opt/audoryn/AgentGate/infrastructure/oracle/restart_core.sh
#
# Each copy stops gracefully (open requests get 20 s); the gateway sends new requests to the other
# copy. The next copy restarts only once this one answers and the gateway has every core copy back in
# rotation. Stops at the first copy that does not come back within 2 minutes; the other keeps serving.
# (The gateway's rollout tool also drains first, but needs httpx, which the host's python3 lacks.)
set -euo pipefail

core_in_rotation() {
    python3 - <<'PY'
import json, sys, urllib.request
pool = json.load(urllib.request.urlopen("http://127.0.0.1:8000/health/gateway", timeout=5))["pools"]["core"]
sys.exit(pool["inRotation"] != pool["copies"])
PY
}

for port in 8101 8102; do
    systemctl --user restart "audoryn-api@$port.service"
    for _ in $(seq 60); do
        if curl -sf "http://127.0.0.1:$port/health/live" >/dev/null && core_in_rotation 2>/dev/null; then
            echo "audoryn-api@$port: back in rotation"
            continue 2
        fi
        sleep 2
    done
    echo "STOP: audoryn-api@$port did not come back within 2 minutes; the other copy keeps serving" >&2
    exit 1
done
