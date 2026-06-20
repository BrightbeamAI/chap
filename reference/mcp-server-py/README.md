# CHAP MCP reference server (Python)

A stdio MCP server that wraps a CHAP Coordinator. The Python
counterpart of [`reference/mcp-server-ts/`](../mcp-server-ts/);
behaviour identical, same 39 tools, different language.

Spec target: MCP 2025-11-25. CHAP 0.2.

## Install

```bash
pip install -e "../../packages/coordinator-py[mcp]"
```

## Run

```bash
python3 server.py
```

You should see, on stderr:

```
CHAP MCP reference server starting on stdio.
Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.
```

The server then waits for MCP protocol messages on stdin and writes
responses to stdout. Kill it with Ctrl+C.

## Wire into Claude Desktop

```json
{
  "mcpServers": {
    "chap": {
      "command": "python3",
      "args": ["/absolute/path/to/chap/reference/mcp-server-py/server.py"]
    }
  }
}
```

Restart Claude Desktop. "chap" should appear as a connected MCP
server with 39 tools.

## What it exposes

Every CHAP method, as an MCP tool named `chap.<method>`. The
Coordinator instance enables every profile out of the box. State is
in-memory and lost on exit.

## See also

- Five-minute walkthrough: [`examples/drive-chap-from-claude-desktop.md`](../../examples/drive-chap-from-claude-desktop.md)
- Underlying adapter: [`chap_coordinator.transports.mcp_server`](../../packages/coordinator-py/chap_coordinator/transports/mcp_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
