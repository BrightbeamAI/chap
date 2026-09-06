# Drive CHAP from Claude Desktop (or any MCP client)

This five-minute walkthrough connects an MCP client to the published CHAP
Coordinator package. You will create a workspace, join human and agent
participants, complete and review a task, record a human override, and read
the resulting evidence chain through natural language.

The example uses Claude Desktop. The same local stdio server works with
Cursor, Claude Code, Continue, Cline, Gemini CLI, and other compatible MCP
clients. Only the client-specific configuration location changes.

No repository clone or global installation is required.

## Prerequisites

- Node.js 20 or later. Confirm with `node --version`.
- An MCP client that supports the MCP 2026-07-28 or 2025-11-25 stdio
  transport.

## Step 1: Add CHAP to your MCP client

For Claude Desktop, open its configuration file:

- **macOS:**
  `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:**
  `%APPDATA%\Claude\claude_desktop_config.json`

Add a `chap` entry under `mcpServers`:

```json
{
  "mcpServers": {
    "chap": {
      "command": "npx",
      "args": [
        "-y",
        "@brightbeamai/chap-coordinator-mcp"
      ]
    }
  }
}
```

If the file already contains other MCP servers, add `chap` alongside them
rather than replacing the existing entries.

For Cursor, place the same `chap` definition inside `.cursor/mcp.json`.

For another MCP client, configure a local stdio server with:

- **Command:** `npx`
- **Arguments:** `-y`, `@brightbeamai/chap-coordinator-mcp`

The client downloads and launches the published package when it needs the
server. For reproducible deployments, pin the package to a tested version,
for example `@brightbeamai/chap-coordinator-mcp@0.2.12`.

## Step 2: Choose whether to persist the workspace

The minimal configuration above runs CHAP in memory. That is suitable for
this walkthrough, but the workspace is discarded when the MCP server exits.

To retain workspaces between restarts, set `CHAP_DB_PATH` to an absolute path
for a SQLite database:

```json
{
  "mcpServers": {
    "chap": {
      "command": "npx",
      "args": [
        "-y",
        "@brightbeamai/chap-coordinator-mcp"
      ],
      "env": {
        "CHAP_DB_PATH": "/absolute/path/to/chap.db"
      }
    }
  }
}
```

Use a real absolute path rather than `~`, because MCP clients do not all
expand shell shortcuts inside environment variables.

The packaged server enables these profiles for new workspaces by default:

- `core/1.0`
- `review/1.0`
- `whisper/1.0`
- `deliberation/1.0`
- `handoff/1.0`
- `control/1.0`
- `routing/1.0`
- `modes/1.0`
- `audit-scitt/1.0`

To use a smaller set, add a comma-separated `CHAP_PROFILES` value to the
`env` object. For example:

```json
"CHAP_PROFILES": "core/1.0,review/1.0,audit-scitt/1.0"
```

## Step 3: Restart and verify

Save the configuration and restart the MCP client. The first launch may take
slightly longer while `npx` downloads the package.

CHAP should appear as a connected MCP server. It exposes 39 tools, with one
tool for every CHAP method. The tool names use the `chap.` prefix.

If your client exposes server logs, the output will report:

- the CHAP MCP server version;
- the enabled profiles; and
- whether it is running in memory or persisting to SQLite.

## Step 4: Create a workspace and task

Open a new chat and enter:

> Create a CHAP workspace called `wsp_demo`. Join me as
> `human:me@local`, a human reviewer, and join a drafting agent as
> `agent:bot@local`. Then create a low-criticality task assigned to the agent
> asking it to draft a response to a customer who wants an update on their
> order.

The client should call:

1. `chap.workspace.create` with `workspace: "wsp_demo"`;
2. `chap.participant.join` twice, once for the human and once for the agent;
3. `chap.task.create` with the agent as assignee.

## Step 5: Complete the task and request review

Enter:

> Mark the task as in progress. Complete it with an output object whose
> `draft` field is "Your order is in transit; the tracking page will update
> within 24 hours", with confidence 0.9. Then open a review of that same
> artefact with `human:me@local` as the reviewer.

The client should call `chap.task.update`, `chap.task.complete`, and
`chap.review.request` in sequence.

## Step 6: Record a human override

Enter:

> Override the draft by replacing "within 24 hours" with "by tomorrow".
> Apply the replacement to the `/draft` field, record the rationale as
> "warmer phrasing", and add the tag `tone-softened`.

The client should call `chap.decide.override` with this JSON Patch operation:

```json
[
  {
    "op": "replace",
    "path": "/draft",
    "value": "Your order is in transit; the tracking page will update by tomorrow"
  }
]
```

The override artefact places the difference, rationale, and tag on the
workspace's audit chain as first-class data.

## Step 7: Read the evidence chain

Enter:

> Read the audit log for `wsp_demo`. Show the entries in sequence, including
> the arrival time, method, actor, task identifier, and previous hash for each
> entry. Verify the local hash chain, then summarise what the agent produced
> and what the human changed.

The client should call `chap.audit.read` and `chap.audit.verify_chain`. You
should see the workspace, participants, task lifecycle, review request, and
override in one ordered, hash-linked record, followed by the chain verdict.

## What you have demonstrated

- An MCP client can drive CHAP through natural language without application
  code.
- The agent's output and the human's intervention remain distinct and
  queryable.
- The override preserves the changed content, the rationale, and a structured
  tag.
- Every action lands in the workspace's append-only evidence log.
- MCP provides the client-to-server transport; CHAP provides the collaboration
  and evidence semantics underneath it.

## Troubleshooting

### The client cannot find `npx`

Run these commands in a terminal:

```bash
node --version
npx --version
```

Install or upgrade to Node.js 20 or later, then restart the MCP client so it
inherits the updated executable path.

### CHAP does not appear as connected

Check that the client configuration is valid JSON, restart the client fully,
and inspect its MCP server logs. The `command` must be `npx`, and the package
name must be exactly `@brightbeamai/chap-coordinator-mcp`.

### Persistence fails to start

`CHAP_DB_PATH` uses the optional native `better-sqlite3` dependency. If the
server reports that the SQLite store cannot be opened, verify that the target
directory exists and is writable. If the native dependency could not be
installed on your platform, install the required build tools or temporarily
remove `CHAP_DB_PATH` to run the walkthrough in memory.

### The client shows fewer than 39 tools

Restart the client and inspect its MCP logs for schema or initialisation
errors. `CHAP_PROFILES` controls which profiles new workspaces advertise; it
does not change the package's 39-tool catalogue.

## What this walkthrough does not prove

The packaged command starts a local stdio server. It is intentionally simple
and should not be treated as a complete production deployment.

- **Authentication:** the stdio process trusts its local MCP client. A remote
  deployment should use an authenticated transport and enforce participant
  identity at the appropriate boundary.
- **Signed identity:** starting the MCP server does not by itself configure
  OIDC identity, signed approvals, or external transparency anchoring.
- **Multi-tenancy:** SQLite persistence does not provide tenant isolation,
  service-level access control, operational monitoring, or high availability.
- **Long-term stability:** CHAP 0.2 is a public draft. Pin versions and review
  release notes when evaluating it in durable workflows.

For a production-oriented service, import `makeChapMcpServer` from
`@brightbeamai/chap-coordinator-mcp`, provide a configured Coordinator, and
attach the transport, authentication, storage, and operating controls required
by the deployment.

## Developing against the repository

If you are modifying CHAP itself rather than evaluating the published package,
run the TypeScript reference server from source:

```bash
git clone https://github.com/BrightbeamAI/chap.git
cd chap/reference/mcp-server-ts
npm install
npm start
```

The Python reference server remains available at
`reference/mcp-server-py/server.py` and requires Python 3.10 or later with the
MCP optional dependencies installed.

## Next steps

- [Understand how CHAP and MCP fit together](../integrations/CHAP-with-MCP.md).
- [Run the protocol-level five-minute start](./00-five-minute-start.md).
- [Inspect the MCP adapter package](../packages/coordinator-mcp/).
- [Read the CHAP specification](../SPECIFICATION.md).
- [View the official MCP Registry metadata](../server.json).
- [Join the CHAP discussion](https://github.com/BrightbeamAI/chap/discussions).

CHAP is a public draft. Testing, implementation reports, critical feedback,
and independent integrations are welcome.
