# CHAP MCP reference server (Python)

A stdio MCP server that wraps a CHAP Coordinator, as an example of embedding
[`chap_coordinator.transports.mcp_server`](../../packages/coordinator-py/chap_coordinator/transports/mcp_server.py).
The Python counterpart of [`reference/mcp-server-ts/`](../mcp-server-ts/):
the same 39 tools, the same participant rule, a different language.

Every call is made as one participant, an agent, so an MCP client can drive
a workspace without being able to act in anyone else's name. For use with a
desk, where a person decides on the assistant's work, run the package's
command instead: `python -m chap_coordinator.transports.mcp_cli --url
http://127.0.0.1:8787/chap` forwards the assistant's calls to a desk server,
and takes the options the npm `chap-mcp-server` takes (`--as`, `--tools`,
`--url`, `--db`, `--profiles`).

Spec target: MCP 2026-07-28, serving 2025-11-25 clients as well. CHAP 0.3.

## Install

```bash
pip install -e "../../packages/coordinator-py[mcp]"
```

## Run

From this directory:

```bash
python3 server.py
```

or by its path from anywhere, which is how an MCP client starts it:

```bash
python3 /absolute/path/to/chap/reference/mcp-server-py/server.py
```

`python -m reference.mcp_server_py.server` cannot work: the directory is
named `mcp-server-py`, and a hyphen cannot appear in a Python module name.
`python -m chap_coordinator.transports.mcp_cli` runs the package's own
command, which works from any directory once the package is installed.

You should see, on stderr:

```
CHAP MCP reference server starting on stdio, acting as agent:assistant@local.
Profiles enabled: core/1.0, review/1.0, whisper/1.0, deliberation/1.0, handoff/1.0, control/1.0, routing/1.0, modes/1.0, audit-scitt/1.0.
```

The server then waits for MCP protocol messages on stdin and writes
responses to stdout. Kill it with Ctrl+C.

`CHAP_PARTICIPANT` names the participant every call is made as; unset, it is
`agent:assistant@local`. A `human:` URI is refused at start: a person decides
in a desk, never through the assistant.

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

Every CHAP method, as an MCP tool named `chap.<method>`, made as the one
participant: `from` is filled in, and a call naming anyone else is refused.
New workspaces get these profiles:

- `core/1.0`
- `review/1.0`
- `whisper/1.0`
- `deliberation/1.0`
- `handoff/1.0`
- `control/1.0`
- `routing/1.0`
- `modes/1.0`
- `audit-scitt/1.0`, so the audit chain is on from a workspace's first entry

The assistant can create a workspace, join it as itself and record its own
work; nothing here can decide as a person. State is in memory and lost on
exit.

## See also

- Five-minute walkthrough: [`examples/drive-chap-from-claude-desktop.md`](../../examples/drive-chap-from-claude-desktop.md)
- Underlying adapter: [`chap_coordinator.transports.mcp_server`](../../packages/coordinator-py/chap_coordinator/transports/mcp_server.py)
- The package's command: [`chap_coordinator.transports.mcp_cli`](../../packages/coordinator-py/chap_coordinator/transports/mcp_cli.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
