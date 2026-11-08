# CHAP A2A reference server (TypeScript)

An HTTP/JSON-RPC A2A server that wraps a CHAP Coordinator over
Express. Point an A2A-aware orchestrator at this and drive a CHAP
workspace as if it were any other A2A agent.

Spec target: A2A 1.0, serving A2A 0.3 clients as well. CHAP 0.3.

## Install

From the repository root, install the workspaces and build the packages this
server imports:

```bash
npm install
npm run build
```

Then run it from this directory.

## Run

```bash
npm start                                  # 127.0.0.1:9090
npm start -- --port 9091
npm start -- --as agent:planner@example.org
npm start -- --skills all                  # every CHAP method
npm start -- --db ~/chap-a2a.db            # keep the workspaces
```

| Option | Environment | Effect |
|--------|-------------|--------|
| `--host <address>` | `CHAP_HOST` | The address to listen on. Default `127.0.0.1`. |
| `--port <number>` | | The port. Default `9090`. |
| `--as <uri>` | `CHAP_PARTICIPANT` | The participant every call is made as, a participant URI. Default `agent:orchestrator@local`. `from` is filled in, a call naming anyone else is refused with `-32011`, and a `human:` URI is refused at start: a person decides in a desk, never through an orchestrator. |
| `--skills agent\|all` | | `agent`, the default, offers the agent's skills, the `AGENT_MCP_TOOLS` set the MCP server offers an assistant. `all` offers every CHAP method, still as the one participant. |
| `--db <path>` | `CHAP_DB_PATH` | Keep the workspaces in this SQLite file; a leading `~` is the home directory. Without it, state is in memory only. |
| `--allowed-host <name>` | `CHAP_ALLOWED_HOSTS` (comma separated) | Another name the server answers to, as a `Host` header carries it, such as `a2a.internal:9090`. Repeatable. |

You should see, on stderr:

```
CHAP A2A reference server starting on http://localhost:9090/, acting as agent:orchestrator@local with the agent skills
Agent Card: http://localhost:9090/.well-known/agent-card.json
Profiles enabled: core/1.0, review/1.0, whisper/1.0, deliberation/1.0, handoff/1.0, control/1.0, routing/1.0, modes/1.0, audit-scitt/1.0.
State is in memory only: the workspaces are lost when this process exits.
Listening on http://localhost:9090/
```

## What it checks

The server answers only under its own names: a request whose `Host` is not
`127.0.0.1`, `localhost` or `[::1]` on its port, or an allowed host, is
refused with 421, so a page that rebinds its own name to this address gets
nothing. A request from a browser on another origin is refused with 403; a
client that is not a browser sends no `Origin`. A POST must be
`application/json` (415), a body over 1,114,112 bytes is refused with 413,
and one that nests deeper than 32 levels with 400. Each refusal is a short
JSON body, and a fault inside the coordinator or the server reaches the
client without its detail, which stays in this process's log. A refused
call is kept as a task, the latest 1,000 of them, without the request.

It has no authentication. Any local process can call it, as the one
participant `--as` names. It binds `127.0.0.1` by default, and warns when
bound to another address. Only expose it on a trusted network.

## Smoke test

```bash
curl http://localhost:9090/.well-known/agent-card.json | head -30
```

A fresh server holds no workspace, and the agent's skills create none, so
start it with `npm start -- --skills all` for the calls below. Send a
`message/send`, as an A2A 0.3 client does. `from` is filled in as the
participant:

```bash
curl -X POST http://localhost:9090/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "m1", "role": "user", "kind": "message",
        "parts": [{
          "kind": "data",
          "data": {
            "skill": "chap.workspace.create",
            "params": {"workspace": "wsp_demo"}
          }
        }]
      }
    }
  }'
```

An A2A 1.0 client sends `SendMessage` with `A2A-Version: 1.0`, and parts
as `{data, mediaType}`:

```bash
curl -X POST http://localhost:9090/ \
  -H "Content-Type: application/json" -H "A2A-Version: 1.0" \
  -d '{
    "jsonrpc": "2.0", "id": "2",
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "m2", "role": "ROLE_USER",
        "parts": [{
          "data": {"skill": "chap.workspace.describe", "params": {"workspace": "wsp_demo"}},
          "mediaType": "application/json"
        }]
      }
    }
  }'
```

A call CHAP refuses comes back as a task in state `TASK_STATE_REJECTED`
(`rejected` to a 0.3 client), whose status message carries the CHAP error.

## What it exposes

The agent's skills, or with `--skills all` every CHAP method, each as an
A2A skill named `chap.<method>` on the agent card, made as the one
participant. New workspaces get the profiles listed above.

## Walkthrough

[`examples/drive-chap-from-an-a2a-orchestrator.md`](../../examples/drive-chap-from-an-a2a-orchestrator.md)

## Architecture

See the [`@brightbeamai/chap-coordinator-a2a`](../../packages/coordinator-a2a/)
package, which this reference uses unchanged.

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
