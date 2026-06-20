# CHAP A2A reference server (Python)

An HTTP/JSON-RPC A2A server that wraps a CHAP Coordinator over
FastAPI + Uvicorn. The Python counterpart of
[`reference/a2a-server-ts/`](../a2a-server-ts/); same behaviour,
different SDK ecosystem.

Spec target: A2A 1.0 with v0.3 compatibility enabled (the Python
`a2a-sdk` 1.x dispatches by PascalCase method names under v1.0; the
server enables `enable_v0_3_compat=True` so the older `message/send`
slash form continues to work). CHAP 0.2.

## Install

```bash
pip install -e "../../packages/coordinator-py[a2a]"
```

## Run

```bash
python3 server.py                # default port 9090
python3 server.py --port 9091
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

Send via the v0.3-compatible slash form:

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

For A2A v1.0 clients, use the PascalCase `SendMessage` method name
instead; the rest is identical.

## What it exposes

Every CHAP method, as an A2A skill named `chap.<method>` on the
agent card. The Coordinator instance enables every profile. State is
in-memory and lost on exit.

## See also

- Walkthrough: [`examples/drive-chap-from-an-a2a-orchestrator.md`](../../examples/drive-chap-from-an-a2a-orchestrator.md)
- Underlying adapter: [`chap_coordinator.transports.a2a_server`](../../packages/coordinator-py/chap_coordinator/transports/a2a_server.py)

## License

Apache 2.0. See the parent repository's [LICENSE](../../LICENSE).
