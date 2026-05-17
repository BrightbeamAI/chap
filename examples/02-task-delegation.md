# Example 02 — Task delegation

**Scenario.** A customer-support agent (human) has a stack of incoming
tickets and delegates drafting the response for a non-urgent refund query
to the triage bot. The bot accepts, works on it, and reports back. We
also show the reverse direction: an agent delegating a follow-up question
to a human.

This example shows:

- `task.assign` from a human to an agent.
- `task.accept` and `task.start` from the agent.
- `task.progress` notifications during the work.
- `task.complete` returning an artefact.
- `task.assign` from the agent back to a human (e.g. a specialist).

---

## 2.1 Human delegates to agent

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T2F1",
  "ts": "2026-05-17T09:14:22.184Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "agent:triage-bot#v3.2",
  "type": "request",
  "method": "task.assign",
  "params": {
    "task": {
      "id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
      "workspace": "wsp_support_triage",
      "kind": "draft_response",
      "state": "created",
      "mode": "production",
      "assignee": "agent:triage-bot#v3.2",
      "delegator": "human:alice@example.org",
      "input": {
        "ticket_id": "INC-48219",
        "customer_message": "Hi — my order #ORD-91204 hasn't arrived after 10 days. Tracking just shows 'in transit'. Please advise.",
        "customer_email": "[email protected]",
        "language": "en",
        "intent_hint": "delivery_delay"
      },
      "constraints": {
        "deadline": "2026-05-17T09:30:00Z",
        "max_tool_calls": 5,
        "permitted_tools": ["order-lookup", "shipping-status"]
      },
      "review": {
        "required": true,
        "reviewers": ["human:alice@example.org"],
        "rule": "any_one_approves"
      }
    }
  },
  "evidence": {
    "prev_hash": "sha256:d7e8…f9a0",
    "sig": "ed25519:alice-2026-05-17:Z8nR…u3JK=="
  }
}
```

The Coordinator validates: Alice has the `task.delegate` scope; the
agent has declared `task.accept`; the task mode (`production`) does not
exceed the workspace ceiling; the permitted tools are a subset of the
workspace's `permitted_mcp_servers`. All clear — it routes to the agent.

---

## 2.2 Agent accepts

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T3C",
  "ts": "2026-05-17T09:14:22.612Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "human:alice@example.org",
  "type": "request",
  "method": "task.accept",
  "params": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "estimated_completion_ms": 4500
  },
  "evidence": {
    "prev_hash": "sha256:e9f0…a1b2",
    "sig": "ed25519:k-2026-05-17a:Y7pT…v4HL=="
  }
}
```

---

## 2.3 Agent reports progress

The agent starts working. It calls two MCP tools and reports progress
along the way:

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T3D",
  "ts": "2026-05-17T09:14:22.790Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "workspace:wsp_support_triage",
  "type": "notification",
  "method": "task.start",
  "params": { "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B" },
  "evidence": { "prev_hash": "sha256:a1b2…c3d4", "sig": "ed25519:…" }
}
```

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T3E",
  "ts": "2026-05-17T09:14:24.103Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "human:alice@example.org",
  "type": "notification",
  "method": "task.progress",
  "params": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "stage": "tool_calls",
    "pct_complete": 40,
    "note": "Looked up order; checking shipping status."
  },
  "evidence": { "prev_hash": "sha256:c3d4…e5f6", "sig": "ed25519:…" }
}
```

---

## 2.4 Agent completes with an artefact

```json
{
  "chap": "0.1",
  "id": "01HZ9YX7K3X8M2V4N6P8R0T3F",
  "ts": "2026-05-17T09:14:27.012Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "human:alice@example.org",
  "type": "request",
  "method": "task.complete",
  "params": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "artefact": {
      "id": "art_01HZ9YX7K3X8M2V4N6P8R0T3G",
      "kind": "draft_response",
      "produced_by": "agent:triage-bot#v3.2",
      "produced_at": "2026-05-17T09:14:27.001Z",
      "task": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
      "content": {
        "subject": "Re: order ORD-91204 delivery delay",
        "body": "Hi, thanks for reaching out about order #ORD-91204.\n\nI checked the carrier tracking — the parcel left our regional sorting facility on May 12 and is currently with the local carrier in your area. The carrier shows a delivery attempt scheduled for tomorrow, May 18. If it doesn't arrive by end of day, please reply to this email and I'll open a missing-parcel investigation right away.\n\nApologies for the delay,\nSupport team",
        "tone": "apologetic",
        "next_action_if_unresolved": "open_investigation"
      },
      "citations": [
        {
          "kind": "mcp_tool_invocation",
          "server": "mcp+https://tools.example.org/orders",
          "tool": "lookup_order",
          "call_id": "call_01HZ9YX7K3X8M2V4N6P8R0T3H",
          "input_hash":  "sha256:b2c3d4e5f6071829304152637485960718293041526374859607182930415263",
          "output_hash": "sha256:d4e5f607182930415263748596071829304152637485960718293041526374a4"
        },
        {
          "kind": "mcp_tool_invocation",
          "server": "mcp+https://tools.example.org/shipping",
          "tool": "carrier_tracking",
          "call_id": "call_01HZ9YX7K3X8M2V4N6P8R0T3J",
          "input_hash":  "sha256:e5f607182930415263748596071829304152637485960718293041526374a4b5",
          "output_hash": "sha256:f6071829304152637485960718293041526374859607182930415263748596c6"
        }
      ],
      "confidence": 0.91,
      "content_hash": "sha256:071829304152637485960718293041526374859607182930415263748596a4b5"
    }
  },
  "evidence": { "prev_hash": "sha256:e5f6…0718", "sig": "ed25519:…" }
}
```

Alice's client receives the completion and shows her the draft. She
moves on to review (see [`03-review-and-approve.md`](./03-review-and-approve.md)).

---

## 2.5 Reverse direction: agent delegates to human

Sometimes an agent encounters a case it shouldn't handle alone — say, a
warranty exception on a non-standard product. It opens a follow-up
task addressed to a human specialist:

```json
{
  "chap": "0.1",
  "id": "01HZ9YZ7K3X8M2V4N6P8R0T3K",
  "ts": "2026-05-17T10:02:11.510Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "human:carol@example.org",
  "type": "request",
  "method": "task.assign",
  "params": {
    "task": {
      "id": "tsk_01HZ9YZ7K3X8M2V4N6P8R0T3L",
      "workspace": "wsp_support_triage",
      "kind": "warranty_review",
      "state": "created",
      "mode": "production",
      "assignee": "human:carol@example.org",
      "delegator": "agent:triage-bot#v3.2",
      "input": {
        "ticket_id": "INC-48227",
        "product_id": "PROD-WX-220",
        "purchase_date": "2024-09-04",
        "issue_summary": "Backlight failure outside standard 12-month warranty; customer cites 18-month statutory protection in their region.",
        "agent_summary": "Customer's region has consumer-protection rules that may extend the warranty. I am not authorised to grant exceptions.",
        "regional_consumer_protection_summary": "Per local statute, electronics carry a 2-year defect liability for the seller."
      },
      "constraints": {
        "deadline": "2026-05-17T18:00:00Z"
      },
      "review": { "required": false }
    }
  },
  "evidence": { "prev_hash": "sha256:0718…29c3", "sig": "ed25519:…" }
}
```

Carol accepts (`task.accept`) and works on it like any other assignment.
The protocol is symmetric: from the wire, an agent-to-human task looks
identical to a human-to-agent task. Only the URIs reveal who is doing
what.

---

## What this gives you

After this exchange:

- **A delegated task with a deadline and bounded tool budget.**
- **A completed artefact with citation hashes** — anyone can later
  fetch the MCP server's audit log and confirm the recorded
  input/output hashes match.
- **Five evidence entries** (assign, accept, start, progress,
  complete) forming an auditable record of what happened, by whom,
  with what tools.

Move on to [`03-review-and-approve.md`](./03-review-and-approve.md) for
the happy-path review of Alice's draft.
