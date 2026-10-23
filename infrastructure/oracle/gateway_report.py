"""What the gateway did, from its log: requests grouped by route, status and copy, with latency, and
the last errors with their correlation ids. Reads `podman logs` on stdin; needs only the host's python3.

    podman logs --since 60m audoryn-gateway 2>&1 | python3 /opt/audoryn/AgentGate/infrastructure/oracle/gateway_report.py

Ids in paths are folded into `:id`, so one route is one row. Nothing here reads headers or query
strings: the gateway never logs them."""
import json
import re
import sys
from collections import defaultdict

ID = re.compile(r"/[0-9a-f]{8}-[0-9a-f-]{27,}|/\d+(?=/|$)")
MARK = '{"event":"gateway.request"'


def main() -> None:
    rows: dict[tuple[str, str, int, str], list[float]] = defaultdict(list)
    errors = []
    for line in sys.stdin:
        start = line.find(MARK)
        if start < 0:
            continue
        request = json.loads(line[start:])
        path = ID.sub("/:id", request["path"])
        rows[(request["method"], path, request["status"], request["upstream"] or "-")].append(request["latencyMs"])
        if request["status"] >= 400:
            errors.append((request["status"], request["method"], path, request["upstream"], request["correlationId"]))
    print(f"{'count':>5} {'p50 ms':>8} {'max ms':>8} {'status':>6}  {'upstream':<11} {'method':<9} path")
    for (method, path, status, upstream), latencies in sorted(rows.items(), key=lambda row: -len(row[1])):
        latencies.sort()
        print(f"{len(latencies):>5} {latencies[len(latencies) // 2]:>8.1f} {latencies[-1]:>8.1f} {status:>6}"
              f"  {upstream:<11} {method:<9} {path}")
    server = sum(status >= 500 for status, *_ in errors)
    print(f"\nrequests: {sum(map(len, rows.values()))} | 4xx: {len(errors) - server} | 5xx: {server}")
    for error in errors[-10:]:
        print("  ", *error)


if __name__ == "__main__":
    main()
