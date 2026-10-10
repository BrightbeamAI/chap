# MCP gate

One process runs the CHAP coordinator, the review desk and an MCP server
over streamable HTTP. Claude Desktop, Cursor or Claude Code connects to it,
and every call the assistant makes that changes state is recorded on the chain. A task the
assistant completes with `review_required` opens a review that waits for you
in the desk, and the assistant cannot approve its own work. No API key is
needed: the assistant you already use is the agent.

## Run it

```bash
npm install
npm start                       # the desk, POST /chap and /mcp on port 8787
open http://127.0.0.1:8787/     # the desk
```

Then connect your MCP client to `http://127.0.0.1:8787/mcp`:

- **Claude Code:** `claude mcp add --transport http chap http://127.0.0.1:8787/mcp`
- **Cursor:** add to `.cursor/mcp.json`:
  `{ "mcpServers": { "chap": { "url": "http://127.0.0.1:8787/mcp" } } }`
- **Claude Desktop:** custom connectors are reached from Anthropic's servers,
  so a server on your machine needs a local bridge. Add to
  `claude_desktop_config.json`:
  `{ "mcpServers": { "chap": { "command": "npx", "args": ["-y", "mcp-remote", "http://127.0.0.1:8787/mcp"] } } }`

Paste `AGENT_INSTRUCTIONS.md` into the assistant's project instructions, or
`CLAUDE.md` for Claude Code, so it knows its participant URI and the
workspace, and asks for review on the work that needs it. The tests need no
client and no model:

```bash
npm test
```

## What you will see

Ask the assistant to draft something that needs your sign-off. It calls
`chap.task.create` with `review_required: true`, does the work, and calls
`chap.task.complete` with the draft as the output. The task moves to
`review_requested` and appears in the desk. Approve it as written, edit it
and override, or reject it. The assistant can read the decision with
`chap.audit.read` and carry on with the decided output. If it tries
`chap.decide.approve` on its own task, the coordinator refuses with `-32011`
and records the attempt.

## Attach your own agent and data

Any MCP client works, and so does anything that can POST JSON: send
JSON-RPC envelopes to `POST /chap`. `desk/chap-client.mjs` is the client the
desk uses and runs under Node too:

```js
import { makeClient } from "./desk/chap-client.mjs";
const agent = makeClient({ url: "http://127.0.0.1:8787/chap", workspace: "__WORKSPACE__", from: "__AGENT_URI__" });
const { task_id } = await agent.call("task.create", { kind: "draft", assignee: "__AGENT_URI__", input: { ticket: "T-1" }, review_required: true });
await agent.call("task.complete", { task_id, output: { body: "..." } });
```

The decided output is at `GET /api/tasks/<task_id>` once the state is no
longer `review_requested`. The participants are in `chap.config.json`; add a
second human there to address reviews to more than one person.

## The desk, insights and analytics

The desk has three views. Review shows each artefact by its shape, with
Approve, Request changes, Reject, and Edit, which approves your version and
records it as an override with your rationale. Activity reads the chain with
what each call did. Insights counts what you did with the agent's work: how
often it was accepted as written, edited, sent back and rejected, the time
to a first decision, and your own notes and rationales, each linked to its
task. `j` and `k` move through the queue, `a`, `r` and `x` decide, and `?`
lists the keys.

`npm run analytics` goes further with `chap-analytics`, the package that
reads a CHAP chain into documented tables. It reads the store under `data/`
and writes the package's interactive report, the evaluation cases and a
refinement page with the corrections ranked to `analytics/`, which the desk
links from Insights. It needs Python 3.10 or later and `pip install
chap-analytics`; `--watch 300` keeps the pages current.

## What each profile changes in this project

The profile list in `chap.config.json` is the one the workspace advertises.
A method a profile owns is refused with `-32601` when the profile is absent.

- `core/1.0`: the workspace, its participants, the tasks and the chain, with
  `audit.read` to read it back; on its own it has no decision call, so a
  draft that asked for review waits and `decide.approve` is refused with
  `-32601`.
- `review/1.0`: the draft waits in the desk; the assistant's own approval is
  refused with `-32011` and recorded; an override's patch is applied by the
  coordinator and the patched result is the task's output.

Adding a profile is one edit to `chap.config.json`. With `modes/1.0` a
trial-mode task requires review whatever the assistant passes; with
`control/1.0` you can pause the assistant and it is assigned nothing
(`-32063`); with `audit-scitt/1.0` the chain is on and the desk shows it
verified.

## diff-profiles

```bash
npm run diff-profiles -- --against core/1.0
```

runs the project's workload under the configured profiles and under
`core/1.0` alone, and prints each call with its outcome under both:

```
The agent approves its own work                               refused -32011          refused -32601   <- differs
```

`--profiles` sets the first list, `--against` the second, and `--json` prints
the rows as a document for `docs/profile-explorer.md`.

## What the deployment supplies

This project does not notify anyone: the desk lists the open reviews
addressed to the reviewer it is asked about, and delivering a review to the
people named on it is the deployment's job (SPECIFICATION 15.1). The MCP
endpoint, the desk, `POST /chap` and the read API under `/api/` have no
login, and a `human:` URI in the desk is a label that says who is deciding;
it does not authenticate them. The server listens on the loopback address,
refuses a request from another origin or under a host name it was not
given, and takes JSON only on `POST /chap`, so a page open elsewhere cannot
decide as the reviewer; a deployment puts TLS and its own login in front,
and names the host the proxy passes in `allowed_hosts` in `chap.config.json`
or in `CHAP_ALLOWED_HOSTS`. It runs one coordinator process over one SQLite
file. The chain head stays in that file,
where the operator of this process can rewrite it; a head published
somewhere the operator cannot change is what shows an entry existed
independently of the coordinator (SECURITY.md).

`tests/` and `diff-profiles.mjs` script the decisions, because nobody is at
the desk when they run; the project itself never does.
