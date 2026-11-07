# CHAP A2A reference server (Python)

An HTTP/JSON-RPC A2A server that wraps a CHAP Coordinator, as an example of
embedding [`chap_coordinator.transports.a2a_app`](../../packages/coordinator-py/chap_coordinator/transports/a2a_app.py).
The Python counterpart of [`reference/a2a-server-ts/`](../a2a-server-ts/);
same behaviour, different SDK ecosystem.

Spec target: A2A 1.0, through `a2a-sdk` 1.x, serving A2A 0.3 clients as
well: the JSON-RPC route has `enable_v0_3_compat`, so the 0.3 `message/send`
form works at the same endpoint as the 1.0 `SendMessage`. CHAP 0.3.

## Install

```bash
pip install -e "../../packages/coordinator-py[a2a]"
```

## Run

```bash
python3 server.py                # 127.0.0.1:9090
python3 server.py --port 9091
python3 server.py --skills all   # every CHAP method
CHAP_ALLOWED_HOSTS=a2a.internal:9090 python3 server.py --host 0.0.0.0
                                 # all interfaces, reached as a2a.internal (see below)
```

Every call is made as the participant `CHAP_PARTICIPANT` names,
`agent:orchestrator@local` when it is unset: `from` is filled in, and a call
naming anyone else is refused with `-32011`. A `human:` URI is refused at
start: a person decides in a desk, never through an orchestrator. It offers
the agent's skills, the `AGENT_MCP_TOOLS` set the MCP server offers an
assistant, unless `--skills all` offers every CHAP method. `--host` falls
back to `CHAP_HOST`, and the card names the address the server listens on,
`localhost` for a loopback or wildcard one.

The package's command serves the same app with every option, `--as`,
`--skills`, `--db` and `--allowed-host` among them:

```bash
python -m chap_coordinator.transports.a2a_cli --port 9090 --db ~/chap-a2a.db
```

You should see, on stderr:

```
CHAP A2A reference server starting on http://localhost:9090/, acting as agent:orchestrator@local with the agent skills
Agent Card: http://localhost:9090/.well-known/agent-card.json
Profiles enabled: core/1.0, review/1.0, whisper/1.0, deliberation/1.0, handoff/1.0, control/1.0, routing/1.0, modes/1.0, audit-scitt/1.0.
```

## What it checks

The app answers only under its own names: a request whose `Host` is not
`127.0.0.1`, `localhost` or `[::1]` on its port, the address it listens on,
or a name in `CHAP_ALLOWED_HOSTS`, is refused with 421, so a page that
rebinds its own name to this address gets nothing. A request from
a browser on another origin is refused with 403; a client that is not a
browser sends no `Origin`. A POST must be `application/json` (415), so a
page's cross-site `fetch` with a `text/plain` body is refused, a body over
1,114,112 bytes is refused with 413 before it is read, and one that nests
deeper than 32 levels with 400; a call whose body is cut short never runs.
Each refusal is a short JSON body, and a fault inside the coordinator or
the server reaches the client without its detail, which stays in the log.
A refused call is kept as a task, the latest 1,000 of them, without the
request, and a whole number reaches the client as an integer.

It has no authentication. Any local process can call it, as the one
participant. It binds `127.0.0.1` by default, and warns when bound to
another address. Only expose it on a trusted network.

## Smoke test

```bash
curl http://localhost:9090/.well-known/agent-card.json | head -30
```

A fresh server holds no workspace, and the agent's skills create none, so
start it with `python3 server.py --skills all` for the call below. Send via
the v0.3-compatible slash form:

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

For A2A 1.0 clients, use the PascalCase `SendMessage` method with the
`A2A-Version: 1.0` header, and parts as `{data, mediaType}`; the TypeScript
server's README shows the request. A call CHAP refuses comes back as a task
in state `TASK_STATE_REJECTED` (`rejected` to a 0.3 client), whose status
message carries the CHAP error.

## What it exposes

The agent's skills, or with `--skills all` every CHAP method, each as an
A2A skill named `chap.<method>` on the agent card, made as the one
participant. New workspaces get the profiles listed above. State is in
memory and lost on exit.

## See also

- Walkthrough: [`examples/drive-chap-from-an-a2a-orchestrator.md`](../../examples/drive-chap-from-an-a2a-orchestrator.md)
- The app and its checks: [`chap_coordinator.transports.a2a_app`](../../packages/coordinator-py/chap_coordinator/transports/a2a_app.py)
- The command: [`chap_coordinator.transports.a2a_cli`](../../packages/coordinator-py/chap_coordinator/transports/a2a_cli.py)
- Underlying adapter: [`chap_coordinator.transports.a2a_server`](../../packages/coordinator-py/chap_coordinator/transports/a2a_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
