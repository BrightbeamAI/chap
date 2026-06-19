"""
CHAP MCP reference server (Python, stdio).

Wraps a CHAP Coordinator and exposes every CHAP method as an MCP
tool over stdio. Point Claude Desktop, Cursor, or any other MCP
client at this script to drive a CHAP workspace from natural
language.

Usage::

    python -m reference.mcp_server_py.server

Or directly::

    python reference/mcp-server-py/server.py

MCP client config (Claude Desktop, ~/Library/Application Support/Claude/claude_desktop_config.json)::

    {
      "mcpServers": {
        "chap": {
          "command": "python3",
          "args": ["/absolute/path/to/chap/reference/mcp-server-py/server.py"]
        }
      }
    }

The coordinator runs in-memory in this process. State is lost when
the process exits.

Spec target: MCP 2025-11-25. CHAP 0.2.
"""
from __future__ import annotations

import asyncio
import sys

from mcp.server.stdio import stdio_server

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.mcp_server import make_chap_mcp_server


async def main() -> None:
    coord = Coordinator(CoordinatorOptions(
        default_profiles=[
            "core/1.0", "review/1.0", "whisper/1.0",
            "deliberation/1.0", "handoff/1.0", "control/1.0",
            "routing/1.0", "audit-scitt/1.0",
        ],
    ))

    server = make_chap_mcp_server(coord, name="chap", version="0.2.3")

    # Log to stderr; stdout is reserved for the MCP protocol stream.
    print("CHAP MCP reference server starting on stdio.", file=sys.stderr)
    print("Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.", file=sys.stderr)

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
