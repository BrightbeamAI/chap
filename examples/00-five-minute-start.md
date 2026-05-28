# 5-Minute Start

This is the fastest path from "I've heard of CHAP" to "I've sent
CHAP envelopes and read back the audit log." It uses the Core
reference server at [`../reference/core/`](../reference/core/) and
plain `curl`. No SDK, no client library.

If you'd rather watch the same flow run as a scripted client, see
[`../reference/core/client.ts`](../reference/core/client.ts).

---

## Step 0: start the server

```bash
cd reference/core
npm install
npm run start:demo
```

You should see:

```
CHAP Core reference listening on http://localhost:8080/chap
```

Leave it running. In another terminal:

---

## Step 1: alice joins

```bash
curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "participant.join",
    "params": {
      "workspace":    "wsp_demo",
      "from":         "human:alice@example.org",
      "to":           "service:coordinator@example.org",
      "ts":           "2026-05-17T09:00:00Z",
      "type":         "human",
      "display_name": "Alice",
      "role":         "reviewer"
    }
  }' | jq
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": { "joined": true, "as": "human:alice@example.org", "role": "reviewer" }
}
```

---

## Step 2: the agent joins

```bash
curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "participant.join",
    "params": {
      "workspace":    "wsp_demo",
      "from":         "agent:triage-bot",
      "to":           "service:coordinator@example.org",
      "ts":           "2026-05-17T09:00:00Z",
      "type":         "agent",
      "display_name": "Triage Bot v0.1",
      "role":         "drafter",
      "capabilities": { "kinds": ["draft_response"] }
    }
  }' | jq
```

---

## Step 3: see who's in the room

```bash
curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "3",
    "method": "workspace.describe",
    "params": {
      "workspace": "wsp_demo",
      "from":      "human:alice@example.org",
      "to":        "service:coordinator@example.org",
      "ts":        "2026-05-17T09:00:00Z"
    }
  }' | jq
```

You'll see both members, the workspace state, and `profiles: ["core/0.2"]`.

---

## Step 4: alice delegates a task to the bot

```bash
curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "4",
    "method": "task.create",
    "params": {
      "workspace": "wsp_demo",
      "from":      "human:alice@example.org",
      "to":        "agent:triage-bot",
      "ts":        "2026-05-17T09:01:00Z",
      "kind":      "draft_response",
      "assignee":  "agent:triage-bot",
      "input":     { "ticket_id": "INC-48219", "customer_message": "Where is my order?" }
    }
  }' | jq
```

The response includes a `task_id`. Save it:

```bash
TASK=$(curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d '{ ... same as above ... }' | jq -r '.result.task_id')
echo $TASK
# tsk_…
```

---

## Step 5: the bot starts work

```bash
curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d "{
    \"jsonrpc\": \"2.0\",
    \"id\": \"5\",
    \"method\": \"task.update\",
    \"params\": {
      \"workspace\":     \"wsp_demo\",
      \"from\":          \"agent:triage-bot\",
      \"to\":            \"human:alice@example.org\",
      \"ts\":            \"2026-05-17T09:01:05Z\",
      \"task_id\":       \"$TASK\",
      \"state\":         \"in_progress\",
      \"progress_note\": \"Looking up the order.\"
    }
  }" | jq
```

---

## Step 6: the bot delivers

```bash
curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d "{
    \"jsonrpc\": \"2.0\",
    \"id\": \"6\",
    \"method\": \"task.complete\",
    \"params\": {
      \"workspace\": \"wsp_demo\",
      \"from\":      \"agent:triage-bot\",
      \"to\":        \"human:alice@example.org\",
      \"ts\":        \"2026-05-17T09:01:10Z\",
      \"task_id\":   \"$TASK\",
      \"output\": {
        \"subject\": \"Re: order status\",
        \"body\":    \"Order ORD-91204 is delayed by the carrier; new ETA Wed.\"
      },
      \"confidence\": 0.91
    }
  }" | jq
```

---

## Step 7: read the audit log

```bash
curl -s -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "7",
    "method": "audit.read",
    "params": {
      "workspace": "wsp_demo",
      "from":      "human:alice@example.org",
      "to":        "service:coordinator@example.org",
      "ts":        "2026-05-17T09:02:00Z",
      "range":     { "from_seq": 0, "to_seq": 100 }
    }
  }' | jq '.result.entries | length, map(.envelope.method)'
```

You get back the full ordered list of envelopes, every join, every
task transition, every progress note. The audit log is the source
of truth.

---

## What just happened

You exercised every one of CHAP Core's 7 methods:

| Method                | What it did                                |
|-----------------------|--------------------------------------------|
| `participant.join`    | Two participants entered the workspace.    |
| `workspace.describe`  | The workspace returned its state.          |
| `task.create`         | A task was created and assigned.           |
| `task.update`         | The assignee reported progress.            |
| `task.complete`       | The assignee delivered output.             |
| `audit.read`          | The full history was readable as a log.    |
| `participant.leave`   | (Skipped here; called by the demo client.) |

That's it. **No crypto. No identity provider. No external services.**
This is a real, useful deployment shape, internal team chatbot,
solo-operator agent farm, structured-task queue.

---

## Where to go next

| You want…                                          | Read                                              |
|----------------------------------------------------|---------------------------------------------------|
| Humans to approve / override agent output          | [`../profiles/review.md`](../profiles/review.md) |
| Mid-task disambiguation questions                  | [`../profiles/whisper.md`](../profiles/whisper.md) |
| Multi-party voting                                 | [`../profiles/deliberation.md`](../profiles/deliberation.md) |
| Safely roll out new agents (shadow → trial → prod) | [`../profiles/modes.md`](../profiles/modes.md) |
| Shift handoffs and follow-the-sun                  | [`../profiles/handoff.md`](../profiles/handoff.md) |
| Pause / supersede / snapshot / rollback            | [`../profiles/control.md`](../profiles/control.md) |
| Non-repudiation and signed messages                | [`../profiles/security-signed.md`](../profiles/security-signed.md) |
| Cryptographic transparency log                     | [`../profiles/audit-scitt.md`](../profiles/audit-scitt.md) |
| Verified human identity                            | [`../profiles/identity-oidc.md`](../profiles/identity-oidc.md) · [`identity-vc.md`](../profiles/identity-vc.md) |
| Cite MCP tool calls inside artefacts               | [`../integrations/CHAP-with-MCP.md`](../integrations/CHAP-with-MCP.md) |
| Cross-organisation peers (A2A bridge)              | [`../integrations/CHAP-with-A2A.md`](../integrations/CHAP-with-A2A.md) |
| Build Core in your favourite language              | [`../core/SPEC.md`](../core/SPEC.md) |
