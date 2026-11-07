"""
CHAP MCP reference server (Python, stdio).

Wraps a CHAP Coordinator and exposes every CHAP method as an MCP tool over
stdio, with every call made as one participant: the agent named by
CHAP_PARTICIPANT, ``agent:assistant`` when it is unset. A call that
names another ``from`` is refused, so the assistant cannot act in a
person's name. A person's decisions belong in a desk: the package's command,
``python -m chap_coordinator.transports.mcp_cli --url ...``, forwards an
assistant's calls to a desk server.

Run it from this directory::

    python3 server.py

or by its path from anywhere, as an MCP client does::

    python3 /absolute/path/to/chap/reference/mcp-server-py/server.py

``python -m reference.mcp_server_py.server`` cannot work: the directory is
named ``mcp-server-py``, and a hyphen cannot appear in a module name.

MCP client config (Claude Desktop, ~/Library/Application Support/Claude/claude_desktop_config.json)::

    {
      "mcpServers": {
        "chap": {
          "command": "python3",
          "args": ["/absolute/path/to/chap/reference/mcp-server-py/server.py"]
        }
      }
    }

The coordinator runs in memory in this process. State is lost when the
process exits; the package's command keeps it with ``--db``.

Spec target: MCP 2026-07-28, serving 2025-11-25 clients as well. CHAP 0.3.
"""
from __future__ import annotations

import asyncio
import os
import sys

from mcp.server.stdio import stdio_server

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.mcp_server import make_chap_mcp_server

PROFILES = [
    "core/1.0", "review/1.0", "whisper/1.0",
    "deliberation/1.0", "handoff/1.0", "control/1.0",
    "routing/1.0", "modes/1.0", "audit-scitt/1.0",
]


async def serve(participant: str) -> None:
    coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES))
    server = make_chap_mcp_server(coord, name="chap", version="0.3.4", participant=participant)

    # Log to stderr; stdout is reserved for the MCP protocol stream.
    print(f"CHAP MCP reference server starting on stdio, acting as {participant}.", file=sys.stderr)
    print(f"Profiles enabled: {', '.join(PROFILES)}.", file=sys.stderr)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> int:
    participant = (os.environ.get("CHAP_PARTICIPANT") or "").strip() or "agent:assistant"
    if participant.startswith("human:"):
        print(f"CHAP_PARTICIPANT {participant} names a person; this server acts for an assistant.",
              file=sys.stderr)
        return 2
    asyncio.run(serve(participant))
    return 0


if __name__ == "__main__":
    sys.exit(main())
