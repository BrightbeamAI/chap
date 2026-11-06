# Drive CHAP from an A2A orchestrator

A five-minute walkthrough. By the end you'll have a local CHAP
Coordinator running as an A2A 1.0 agent, which A2A 0.3 clients reach
as well, exposed to any A2A-aware orchestrator that can register an
agent by URL. The orchestrator works in the coordinator as one
participant.

This is the A2A counterpart to
[`drive-chap-from-claude-desktop.md`](./drive-chap-from-claude-desktop.md):
same CHAP coordinator underneath, different transport on top.

## Prerequisites

- Either Python 3.10+ (the package's command, no clone needed) or
  Node.js 22+ and a clone of the repository (the TypeScript reference
  server). Both expose the same 39 skills.
- An A2A client. The walkthrough uses raw `curl` to keep it
  self-contained; substitute Azure AI Foundry, Amazon Bedrock
  AgentCore, Google ADK, or your own orchestrator for the real
  thing.

## Step 1: Start a server

**Python:**

```bash
pip install "chap-coordinator[a2a]"
python -m chap_coordinator.transports.a2a_cli --port 9090
```

**TypeScript**, from a clone:

```bash
git clone https://github.com/BrightbeamAI/chap.git
cd chap
npm install
npm run build
cd reference/a2a-server-ts
npm start -- --port 9090
```

Either way stderr says where the server listens, the participant it acts
as, and that its state is in memory only:

```
... on http://localhost:9090/, acting as agent:orchestrator@local
Agent Card: http://localhost:9090/.well-known/agent-card.json
```

Every call is made as that participant, `agent:orchestrator@local`;
`--as` names another. A call that names anyone else is refused, and the
server will not start as a `human:` participant: a person decides in a
desk, never through an orchestrator.

## Step 2: Discover capabilities

```bash
curl -s http://localhost:9090/.well-known/agent-card.json -H "A2A-Version: 1.0" | head -c 600
```

The card lists the JSON-RPC endpoint at protocol version 1.0 and at 0.3,
names `application/json` as every input and output mode, and has 39 skills,
each named `chap.<method>`. Without the `A2A-Version` header the server
answers in the card's 0.3 shape, with the `url` an A2A 0.3 client reads.

## Step 3: Drive the coordinator

The skill id and its params go in a data part of the message.
`from` is filled in as the server's participant, so the calls leave it out:

```bash
curl -s -X POST http://localhost:9090/ \
  -H "Content-Type: application/json" -H "A2A-Version: 1.0" \
  -d '{
    "jsonrpc": "2.0", "id": "1",
    "method": "SendMessage",
    "params": {
      "message": {
        "messageId": "m1", "role": "ROLE_USER",
        "parts": [{
          "data": {"skill": "chap.workspace.create", "params": {"workspace": "wsp_a2a_demo"}},
          "mediaType": "application/json"
        }]
      }
    }
  }'
```

The answer is an agent message whose data part is the result:

```json
{
  "jsonrpc": "2.0", "id": "1",
  "result": {
    "message": {
      "role": "ROLE_AGENT",
      "parts": [{
        "data": {"workspace": "wsp_a2a_demo", "created": "..."},
        "mediaType": "application/json"
      }]
    }
  }
}
```

Join the workspace as the orchestrator, and open a task:

```bash
send() {
  curl -s -X POST http://localhost:9090/ \
    -H "Content-Type: application/json" -H "A2A-Version: 1.0" \
    -d "{\"jsonrpc\":\"2.0\",\"id\":\"$1\",\"method\":\"SendMessage\",\"params\":{\"message\":{\"messageId\":\"m$1\",\"role\":\"ROLE_USER\",\"parts\":[{\"data\":$2,\"mediaType\":\"application/json\"}]}}}"
  echo
}
send 2 '{"skill":"chap.participant.join","params":{"workspace":"wsp_a2a_demo","type":"agent","role":"drafter"}}'
send 3 '{"skill":"chap.task.create","params":{"workspace":"wsp_a2a_demo","kind":"summary","assignee":"agent:orchestrator@local","input":{"source":"Q3 report"}}}'
```

Then read the log back and verify its chain:

```bash
send 4 '{"skill":"chap.audit.read","params":{"workspace":"wsp_a2a_demo"}}'
send 5 '{"skill":"chap.audit.verify_chain","params":{"workspace":"wsp_a2a_demo"}}'
```

Each entry names its sender, `agent:orchestrator@local`, and the chain
verifies.

## Step 4: See what the orchestrator cannot do

Complete the task, with the `task_id` from step 3:

```bash
send 6 '{"skill":"chap.task.complete","params":{"workspace":"wsp_a2a_demo","task_id":"tsk_...","output":{"summary":"Revenue up 4%."}}}'
```

A call CHAP refuses is a failure in A2A's terms: the answer is a task in
state `TASK_STATE_REJECTED`, whose status message carries the CHAP error.
Here it is `-32011`: the workspace runs in trial mode (`modes/1.0`), where
work needs a person's review before it completes, and the workspace has
no person who could review it. The refused attempt is on the log.

Nor can the orchestrator act in that person's name:

```bash
send 7 '{"skill":"chap.participant.join","params":{"workspace":"wsp_a2a_demo","from":"human:alice@example.org","type":"human"}}'
```

This is refused with `-32011` too, before it reaches the coordinator: the
server acts as the orchestrator only. A call the coordinator could not
complete at all is a task in `TASK_STATE_FAILED`. An orchestrator that
reads only task states sees each failure.

## Clients on A2A 0.3

A client still on A2A 0.3 sends `message/send` with `kind`-tagged parts,
and no `A2A-Version` header, to the same URL:

```bash
curl -s -X POST http://localhost:9090/ \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0", "id": "8",
    "method": "message/send",
    "params": {
      "message": {
        "messageId": "m8", "role": "user", "kind": "message",
        "parts": [{
          "kind": "data",
          "data": {"skill": "chap.workspace.describe", "params": {"workspace": "wsp_a2a_demo"}}
        }]
      }
    }
  }'
```

The answer comes in 0.3 shapes: `"kind": "message"`, or for a refusal
`"kind": "task"` with `status.state` `rejected`.

## What you've demonstrated

- An external A2A orchestrator can drive every CHAP method via the
  agent's published skills, without writing any CHAP-specific code,
  as one participant.
- A refused call is a rejected task, which an orchestrator can tell
  from an answer without reading CHAP's error codes.
- Work that needs a person's review waits for one, and the
  orchestrator cannot act in a person's name.
- Every call lands in the workspace audit log as a CHAP envelope. The
  log is hash-linked, since the server's workspaces advertise
  `audit-scitt/1.0`: each entry carries the hash of the one before it,
  and `chap.audit.verify_chain` checks them. The calls are not signed;
  signatures need `security-signed/1.0` and a client that signs.
- Skill names align with the MCP transport's tool names, so
  documentation, client code, and operator mental models port
  across protocols.

## What this does not include

- **Authentication.** The server answers only under its own names,
  refuses a browser request from another origin, takes JSON only and
  caps the body, which keeps a web page the user visits from calling it.
  It does not identify its callers: any local process can call it, as
  its one participant. For a deployment, layer A2A's security schemes,
  declared on the card, and your auth model (OAuth, mTLS, API key) at
  the HTTP transport.
- **Persistence.** State is in memory. `--db <path>` keeps the
  workspaces in a SQLite file, in the Python command and the TypeScript
  reference server alike.
- **A person's decisions.** The server acts only as the orchestrator,
  so nothing here decides as a person; a review waits for a person who
  decides in a desk, such as the one a `create-chap-app` project runs.
- **A narrower orchestrator.** The server offers every skill, so the
  orchestrator can also request reviews and join a workspace as itself.
  Where it should do neither, give the card and the executor the same
  skill filter, as the adapter's README shows with the MCP server's agent
  tool set.

See `integrations/CHAP-with-A2A.md` §8 for the full integration
picture, including the **other** direction: how an A2A exchange
made *from* a CHAP workspace cites its provenance in the local
audit log via the bridge-participant pattern and the
`wrap_a2a_message_exchange` library helper.
