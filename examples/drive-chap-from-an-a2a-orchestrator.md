# Drive CHAP from an A2A orchestrator

A five-minute walkthrough. By the end you'll have a local CHAP
Coordinator running as an A2A 0.3.0 / 1.0 agent, exposed to any
A2A-aware orchestrator that can register an agent by URL.

This is the A2A counterpart to
[`drive-chap-from-claude-desktop.md`](./drive-chap-from-claude-desktop.md):
same CHAP coordinator underneath, different transport on top.

## Prerequisites

- Either Node.js 18+ (for the TypeScript reference) or Python 3.10+
  (for the Python reference). Both expose the same 39 skills.
- An A2A client. The walkthrough uses raw `curl` to keep it
  self-contained; substitute Azure AI Foundry, Amazon Bedrock
  AgentCore, Google ADK, or your own orchestrator for the real
  thing.

## Step 1: Start the reference server

**TypeScript** (A2A spec 0.3.0):

```bash
cd reference/a2a-server-ts
npm install
npm start -- --port 9090
```

**Python** (A2A spec 1.0, with v0.3 compatibility enabled):

```bash
cd packages/coordinator-py
pip install -e ".[a2a]"
python3 ../../reference/a2a-server-py/server.py --port 9090
```

Either way you should see:

```
CHAP A2A reference server starting on http://localhost:9090
Agent Card: http://localhost:9090/.well-known/agent-card.json
Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.
```

## Step 2: Discover capabilities

```bash
curl -s http://localhost:9090/.well-known/agent-card.json | head -30
```

You'll see the agent card with 39 skills, each named
`chap.<method>`. An orchestrator that lists this agent will pick up
all 39 capabilities automatically.

## Step 3: Drive the coordinator

The skill id goes in the message's `data` blob; params follow.

```bash
curl -s -X POST http://localhost:9090/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "m1",
        "role": "user",
        "kind": "message",
        "parts": [{
          "kind": "data",
          "data": {
            "skill": "chap.workspace.create",
            "params": {"workspace": "wsp_a2a_demo"}
          }
        }]
      }
    }
  }' | python3 -m json.tool
```

Response (truncated):

```json
{
  "result": {
    "kind": "message",
    "role": "agent",
    "parts": [{
      "kind": "data",
      "data": {"workspace": "wsp_a2a_demo", "created": "..."}
    }]
  }
}
```

Now join a participant:

```bash
curl -s -X POST http://localhost:9090/ \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"2","method":"message/send","params":{"message":{"messageId":"m2","role":"user","kind":"message","parts":[{"kind":"data","data":{"skill":"chap.participant.join","params":{"workspace":"wsp_a2a_demo","from":"agent:remote-bot","type":"agent","role":"drafter"}}}]}}}'
```

And so on for every CHAP method. Every call ends up as a
chain-linked entry in the workspace audit log.

## Note for the Python server

The Python `a2a-sdk` 1.x dispatches by PascalCase method name
(`SendMessage`, `GetTask`) under spec v1.0, but the reference server
enables `enable_v0_3_compat=True` so the older slash form
(`message/send`) keeps working from the same endpoint. The
TypeScript reference is on spec v0.3.0 and uses the slash form
exclusively.

If your orchestrator targets A2A v1.0, drop the `message/send`
method name in the example above and use `SendMessage` instead; the
rest is identical.

## What you've just demonstrated

- An external A2A orchestrator can drive every CHAP method via the
  agent's published skills, without writing any CHAP-specific code.
- Every call lands in the workspace audit log as a real CHAP
  envelope, signature-linked, with the same semantics as if a code
  client had made the call.
- Skill names align with the MCP transport's tool names, so
  documentation, client code, and operator mental models port
  across protocols.

## What this does not include

The reference server is in-memory and unauthenticated. For
production, layer the usual A2A security schemes (`AgentCard.security`,
`security_schemes`) plus your preferred auth model (OAuth, mTLS,
API key) at the HTTP transport. Persistence comes via the
Coordinator's `onAudit` listener and `snapshot()` / `restore()`
methods, same pattern as the MCP reference.

See `integrations/CHAP-with-A2A.md` §8 for the full integration
picture, including the **other** direction: how an A2A exchange
made *from* a CHAP workspace cites its provenance in the local
audit log via the bridge-participant pattern and the
`wrap_a2a_message_exchange` library helper.
