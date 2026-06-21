"""
CHAP A2A reference server (Python, HTTP/JSON-RPC).

Wraps a CHAP Coordinator and serves it as an A2A 1.0 agent. Other
A2A-aware orchestrators (Azure AI Foundry, Amazon Bedrock AgentCore,
Google ADK, etc.) can register this agent by its base URL, discover
its capabilities at ``/.well-known/agent-card.json``, and delegate
work to it via ``message/send``.

Usage::

    python reference/a2a-server-py/server.py [--port 9090]

The coordinator runs in-memory in this process. State is lost when
the process exits.

Spec target: A2A 1.0 (via a2a-sdk 1.x). CHAP 0.2.
"""
from __future__ import annotations

import argparse
import logging
import sys

import uvicorn
from fastapi import FastAPI

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
)
from a2a.server.tasks import InMemoryTaskStore

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.a2a_server import (
    make_chap_agent_card,
    make_chap_agent_executor,
)


def build_app(base_url: str = "http://localhost:9090") -> FastAPI:
    coord = Coordinator(CoordinatorOptions(
        default_profiles=[
            "core/1.0", "review/1.0", "whisper/1.0",
            "deliberation/1.0", "handoff/1.0", "control/1.0",
            "routing/1.0", "audit-scitt/1.0",
        ],
    ))

    agent_card = make_chap_agent_card(base_url=base_url)
    executor = make_chap_agent_executor(coord)
    task_store = InMemoryTaskStore()

    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
    )

    app = FastAPI(
        title="CHAP A2A Reference Server",
        description="CHAP Coordinator exposed as an A2A 1.0 agent.",
        version="0.2.5",
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True),
    )
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

    print(f"CHAP A2A reference server starting on {base_url}", file=sys.stderr)
    print(f"Agent Card: {base_url}/.well-known/agent-card.json", file=sys.stderr)
    print("Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.", file=sys.stderr)

    uvicorn.run(build_app(base_url=base_url), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
