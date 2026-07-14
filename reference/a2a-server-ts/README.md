# CHAP A2A reference server (TypeScript)

An HTTP/JSON-RPC A2A server that wraps a CHAP Coordinator over
Express. Point an A2A-aware orchestrator at this and drive a CHAP
workspace as if it were any other A2A agent.

Spec target: A2A 0.3.0 (the version implemented by `@a2a-js/sdk`).
CHAP 0.2.

## Install

```bash
npm install
```

## Run

```bash
npm start              # default port 9090
npm start -- --port 9091
```

You should see, on stderr:

```
CHAP A2A reference server starting on http://localhost:9090
Agent Card: http://localhost:9090/.well-known/agent-card.json
Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.
```

## Smoke test

```bash
curl http://localhost:9090/.well-known/agent-card.json | head -30
```

Send a `message/send`:

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

## What it exposes

Every CHAP method, as an A2A skill named `chap.<method>` on the
agent card. The Coordinator instance enables every profile. State is
in-memory and lost on exit.

## Walkthrough

[`examples/drive-chap-from-an-a2a-orchestrator.md`](../../examples/drive-chap-from-an-a2a-orchestrator.md)

## Architecture

See the [`@brightbeamai/chap-coordinator-a2a`](../../packages/coordinator-a2a/)
package, which this reference uses unchanged.

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
