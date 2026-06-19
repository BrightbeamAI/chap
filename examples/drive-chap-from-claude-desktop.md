# Drive CHAP from Claude Desktop (or any MCP client)

A five-minute walkthrough. By the end you'll have Claude Desktop
talking to a local CHAP Coordinator and creating a workspace, joining
participants, drafting and reviewing a task, and reading the audit
log, all through natural language.

Same instructions work for Cursor, Claude Code, Continue, or any
other MCP-aware client. Substitute their config file location and
syntax; the CHAP server side is identical.

## Prerequisites

- An MCP client. This walkthrough assumes Claude Desktop, but any
  client that speaks the MCP 2025-11-25 stdio transport will work.
- Either Node.js 18+ (for the TypeScript reference) or Python 3.10+
  (for the Python reference). Pick whichever you'd rather debug in;
  both expose the same 39 tools and behave identically over the wire.

## Step 1: Get the CHAP repo

```bash
git clone https://github.com/BrightbeamAI/chap.git
cd chap
```

## Step 2: Install and start the reference server

**TypeScript:**

```bash
cd reference/mcp-server-ts
npm install
# Optional: verify it starts cleanly. Ctrl+C to exit.
npm start
```

**Python:**

```bash
cd packages/coordinator-py
pip install -e ".[mcp]"
# Optional: verify it starts cleanly. Ctrl+C to exit.
python3 ../../reference/mcp-server-py/server.py
```

Either way you should see two lines on stderr:

```
CHAP MCP reference server starting on stdio.
Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.
```

If you see that, the server's working. Kill it; the MCP client will
launch its own copy.

## Step 3: Wire it into Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent on your OS. Add a `chap` entry under
`mcpServers`. Pick the language that matches what you installed in
Step 2.

**TypeScript:**

```json
{
  "mcpServers": {
    "chap": {
      "command": "tsx",
      "args": ["/absolute/path/to/chap/reference/mcp-server-ts/server.ts"]
    }
  }
}
```

**Python:**

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

Restart Claude Desktop. You should see "chap" appear as a connected
MCP server in the lower-right of the chat input, and the tool count
should read 39.

## Step 4: Try it

Open a new chat in Claude Desktop and type:

> Create a CHAP workspace called `wsp_demo`, join me as `human:me@local` (a human reviewer) and a drafting agent as `agent:bot@local`. Then create a task assigned to the bot asking it to draft a response to a customer who's asking about their order status, with low criticality.

Claude will call:

1. `chap.workspace.create` with `workspace: "wsp_demo"`
2. `chap.participant.join` twice (once for you, once for the bot)
3. `chap.task.create` with the bot as assignee

Then ask:

> Now mark the task in progress, complete it with a draft saying "Your order is in transit; the tracking page will update within 24 hours", confidence 0.9, and open a review with me as the reviewer.

Claude will call `chap.task.update`, `chap.task.complete`, and
`chap.review.request` in sequence.

Finally:

> Override that draft to replace "within 24 hours" with "by tomorrow",
> rationale "warmer phrasing", tagged as `tone-softened`.

Claude calls `chap.decide.override` with a JSON Patch. The override
artefact (diff + rationale + tags) lands in the workspace's audit log
exactly as a code-driven CHAP client would have produced it.

Ask Claude to show you `chap.audit.read` on the workspace and you'll
see the chain of envelopes Claude itself drove.

## What you've just demonstrated

- An LLM client can drive every CHAP method from natural language,
  without writing any code.
- Every action lands in the workspace's audit log as a structured
  envelope, chain-linked, with the override artefact carrying the
  diff and rationale as first-class data.
- The same wire protocol (JSON-RPC 2.0) carries both directions of
  the conversation. MCP is the LLM-facing transport; CHAP is the
  governance protocol underneath.

## What this does not include

The reference server is in-memory and unauthenticated. For production:

- **Persistence**: layer a state store on top via the Coordinator's
  `onAudit` listener and `snapshot()` / `restore()` methods, or use
  the same approach as `reference/playground/src/state-store.ts`.
- **Auth**: MCP's OAuth 2.1 auth model is implemented by the
  Streamable HTTP transport; deploy that instead of stdio and apply
  the usual API-key / bearer-token gating at the transport layer.
- **Multi-tenant**: one Coordinator can serve many workspaces; one
  MCP server can serve many clients. Run the server as a service,
  not as a per-client subprocess.

See `integrations/CHAP-with-MCP.md` for the full integration picture
(including the other direction: how an MCP tool call inside a CHAP
workspace cites its provenance in the audit log).
