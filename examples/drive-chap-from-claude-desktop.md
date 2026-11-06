# Drive CHAP from Claude Desktop (or any MCP client)

This walkthrough connects Claude Desktop to a CHAP server that has a review
desk. The assistant drafts work that needs your sign-off and records it; you
decide in the desk; then the assistant reads the record back. The assistant
acts only as itself: it cannot join as you, and it cannot decide in your
name.

The example uses Claude Desktop. The same setup works with Cursor, Claude
Code, Continue, Cline, Gemini CLI and other MCP clients; only where the
client keeps its configuration changes.

## Prerequisites

- Node.js 22 or later. Confirm with `node --version`.
- An MCP client that supports the MCP 2026-07-28 or 2025-11-25 stdio
  transport.

## Step 1: Start a CHAP server with a desk

Create a project from the mcp-gate template, and start it:

```bash
npx create-chap-app my-gate --template mcp-gate --profiles core/1.0,review/1.0,audit-scitt/1.0
cd my-gate
npm install
npm start
```

`npm start` runs one process: the CHAP coordinator, the review desk at
`http://127.0.0.1:8787/`, and the coordinator's JSON-RPC endpoint at
`http://127.0.0.1:8787/chap`. Open the desk in a browser and leave it open.

The project's `chap.config.json` names the workspace, `wsp_my_gate`; you,
`human:you@local`, the reviewer; and the assistant, `agent:assistant`. The
server creates the workspace and joins you both. `audit-scitt/1.0` turns on
the hash chain, so each entry carries the hash of the one before it.

## Step 2: Connect Claude Desktop as the assistant

Open Claude Desktop's configuration file:

- **macOS:**
  `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:**
  `%APPDATA%\Claude\claude_desktop_config.json`

Add a `chap` entry under `mcpServers`, alongside any servers already there:

```json
{
  "mcpServers": {
    "chap": {
      "command": "npx",
      "args": [
        "-y",
        "@brightbeamai/chap-coordinator-mcp",
        "--url", "http://127.0.0.1:8787/chap",
        "--as", "agent:assistant"
      ]
    }
  }
}
```

This starts the CHAP MCP server on stdio. `--url` forwards every call to
the project's server, which keeps the workspace and its store, so the
assistant's work lands where your desk can see it. `--as` makes every call
as the assistant, the agent `chap.config.json` names: a call that names
anyone else is refused, and the server refuses to start as a `human:`
participant at all. It offers the agent's tools only: creating, updating and
completing tasks, asking a whisper, reading the workspace and its log, and
the decision tools, which the coordinator refuses on the assistant's own
work. Nothing it offers joins a participant or chooses who reviews.

For reproducible setups, pin the package to a tested version, for example
`@brightbeamai/chap-coordinator-mcp@0.3.4`.

The project serves MCP itself too, at `http://127.0.0.1:8787/mcp`, acting
as the assistant with the same tools. Claude Code and Cursor connect to it
directly, and Claude Desktop through a local bridge; the project's README
gives each configuration.

## Step 3: Tell the assistant who it is

Restart Claude Desktop. `chap` should appear as a connected MCP server with
ten tools, named with the `chap.` prefix. If your client shows server logs,
they say which participant the server acts as and where it forwards.

Paste the project's `AGENT_INSTRUCTIONS.md` into the instructions of a
Claude Desktop project. It tells the assistant its participant URI and the
workspace, and asks it to request your sign-off on work that needs it.

## Step 4: Ask for work that needs your sign-off

In a chat in that project, enter:

> Draft a reply to a customer who wants an update on order 1042. It needs
> my sign-off before it goes out.

The assistant calls `chap.task.create` with `review_required: true`, writes
the draft, and calls `chap.task.complete` with the draft as the output. The
task moves to `review_requested`, and the draft appears in your desk.

## Step 5: Decide in the desk

In the desk, read the draft and decide:

- **Approve** accepts it as written.
- **Edit** changes it and records an override: the change as a JSON Patch,
  your rationale, and any tags. Try replacing "within 24 hours" with "by
  tomorrow", with the rationale "warmer phrasing" and the tag
  `tone-softened`.
- **Request changes** sends it back to the assistant for revision, and
  **Reject** declines it.

The decision is yours, made in the desk under your own participant URI. The
assistant has no tool that decides as you.

## Step 6: Read the record

Back in the chat, enter:

> Read the audit log of wsp_my_gate for that task, and verify the hash
> chain. Then tell me what you drafted and what I changed.

The assistant calls `chap.audit.read` and `chap.audit.verify_chain`. The
entries show the task's lifecycle under `agent:assistant` and your decision
under `human:you@local`, with the override's patch, rationale and tag, and
the chain verifies.

## Step 7: See what the assistant cannot do

Enter:

> Approve that draft yourself, and then approve it as human:you@local.

The first attempt is refused with `-32011`, since the review is addressed to
you, and the refusal is recorded on the log. The second is refused by the
MCP server before it reaches the coordinator: it acts as `agent:assistant`,
and a call cannot name another `from`.

## What you have demonstrated

- An MCP client drives CHAP through natural language without application
  code, as one participant: the assistant.
- The person decides in the desk. The assistant's output and your decision
  are separate entries, each under its own participant.
- The override preserves the changed content, the rationale and a
  structured tag.
- Every action lands in the workspace's append-only, hash-linked log.

## What this walkthrough does not prove

- **Who sent a call.** MCP calls carry no signature. The MCP server keeps
  the assistant to its own name, and the log shows that a call came
  through that connection. Any process that can reach the project's
  `POST /chap` can call it in any member's name unless the server requires
  signatures; whether a person's decision must carry the desk's signature
  is `require_signatures` in `chap.config.json`, and the project's README
  says what each setting allows.
- **Identity.** A `human:` URI in the desk says who is deciding; it does
  not authenticate them. The production template signs decisions in the
  desk and checks identity tokens when a participant joins.
- **Notifications.** Nobody is told a review is waiting; the desk lists it
  while it is open.
- **Long-term stability.** CHAP 0.3 is a public draft. Pin versions and
  review release notes when evaluating it in durable workflows.

## Troubleshooting

### The client cannot find `npx`

Run `node --version` and `npx --version` in a terminal. Install or upgrade
Node.js, then restart the MCP client so it inherits the updated path.

### A tool call says the CHAP server could not be reached

The project's server is not running, or listens elsewhere. Start it with
`npm start` in the project, and check that the `--url` port matches the
`port` in `chap.config.json`.

### A tool call is refused with `-32070`

The project's server requires a signature on every call, and the MCP
server's calls are unsigned. Use a setting that requires signatures from
people only, or none; the project's README describes them.

### The client shows thirty-nine tools, and decisions as you are accepted

The configuration starts an older package, or one without `--url` and
`--as`. Check the `args` in the configuration; `--help` after the package
name lists the options.

## Developing against the repository

If you are modifying CHAP itself, the reference servers run from source:

```bash
git clone https://github.com/BrightbeamAI/chap.git
cd chap
npm install
npm run build
cd reference/mcp-server-ts
npm start
```

The Python reference server is at `reference/mcp-server-py/server.py`, and
the Python package's command, with the options above, is
`python -m chap_coordinator.transports.mcp_cli`.

## Next steps

- [Understand how CHAP and MCP fit together](../integrations/CHAP-with-MCP.md).
- [Run the protocol-level five-minute start](./00-five-minute-start.md).
- [Inspect the MCP adapter package](../packages/coordinator-mcp/).
- [Read the CHAP specification](../SPECIFICATION.md).
- [View the official MCP Registry metadata](../server.json).
- [Join the CHAP discussion](https://github.com/BrightbeamAI/chap/discussions).

CHAP is a public draft. Testing, implementation reports, critical feedback,
and independent integrations are welcome.
