"""
CHAP A2A reference server (Python, HTTP/JSON-RPC).

Wraps a CHAP Coordinator and serves it as an A2A 1.0 agent, answering A2A
0.3 clients as well. Other A2A-aware orchestrators (Azure AI Foundry, Amazon
Bedrock AgentCore, Google ADK, etc.) can register this agent by its base
URL, discover its capabilities at ``/.well-known/agent-card.json``, and
delegate work to it via ``SendMessage``, or ``message/send`` at 0.3.

An example of embedding the package's app: every call is made as the
participant CHAP_PARTICIPANT names, ``agent:orchestrator@local`` when it is
unset, and the app answers only under its own names (421; CHAP_ALLOWED_HOSTS
adds names, comma separated), refuses a browser request from another origin
(403), takes JSON only (415) and caps the body (413).
``python -m chap_coordinator.transports.a2a_cli`` serves the same app with
every option, ``--as``, ``--db`` and ``--allowed-host`` among them.

Usage::

    python reference/a2a-server-py/server.py [--host 127.0.0.1] [--port 9090]

The coordinator runs in memory in this process. State is lost when the
process exits.

Spec target: A2A 1.0 (via a2a-sdk 1.x), serving 0.3 clients as well. CHAP 0.3.
"""
from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.a2a_app import make_chap_a2a_app

PROFILES = [
    "core/1.0", "review/1.0", "whisper/1.0",
    "deliberation/1.0", "handoff/1.0", "control/1.0",
    "routing/1.0", "modes/1.0", "audit-scitt/1.0",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="CHAP A2A reference server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()

    participant = (os.environ.get("CHAP_PARTICIPANT") or "").strip() or "agent:orchestrator@local"
    if participant.startswith("human:"):
        print(f"CHAP_PARTICIPANT {participant} names a person; this server acts for an agent.", file=sys.stderr)
        return 2
    if args.host not in ("127.0.0.1", "::1", "localhost"):
        print(f"WARNING: binding to {args.host} exposes an unauthenticated "
              "coordinator to the network. Use only on a trusted network.",
              file=sys.stderr)

    # Names a client may reach it by besides its loopback names: those in
    # CHAP_ALLOWED_HOSTS, and the address it listens on.
    allowed_hosts = [h.strip() for h in (os.environ.get("CHAP_ALLOWED_HOSTS") or "").split(",") if h.strip()]
    if args.host not in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "::"):
        allowed_hosts.append(f"[{args.host}]:{args.port}" if ":" in args.host else f"{args.host}:{args.port}")

    base_url = f"http://localhost:{args.port}/"
    coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES))
    app = make_chap_a2a_app(coord, base_url=base_url, participant=participant,
                            allowed_hosts=allowed_hosts, version="0.3.4")

    print(f"CHAP A2A reference server starting on {base_url}, acting as {participant}", file=sys.stderr)
    print(f"Agent Card: {base_url}.well-known/agent-card.json", file=sys.stderr)
    print(f"Profiles enabled: {', '.join(PROFILES)}.", file=sys.stderr)

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
