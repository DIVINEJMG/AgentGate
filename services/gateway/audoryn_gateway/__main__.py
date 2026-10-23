"""Runs the gateway: `python -m audoryn_gateway [--host 127.0.0.1] [--port 8000]` from services/gateway.

The gateway is the edge, so uvicorn does not trust `X-Forwarded-*` from callers (the gateway sets
them itself) and its own access log is off (the gateway writes one line per request)."""
from __future__ import annotations

import argparse
import logging

import uvicorn

from audoryn_gateway.app import app, gateway


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=gateway.settings.log_level, format="%(levelname)s:     %(message)s")
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=False, access_log=False,
                timeout_graceful_shutdown=30, timeout_keep_alive=gateway.settings.keep_alive_seconds)


if __name__ == "__main__":
    main()
