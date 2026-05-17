# Example 01 — Discovery

**Scenario.** A new agent comes online and joins a customer-support triage
workspace. Before it can accept work, it announces itself, and an admin
fetches its descriptor to verify its capabilities and signing key.

This example shows the three discovery operations:

1. `participant.announce` — an agent advertises itself.
2. `workspace.describe` — fetching the workspace state.
3. `participant.describe` — fetching a specific Participant's descriptor.

All messages are signed; signatures are abbreviated for readability.

---

## 1.1 The agent announces itself

The agent connects to the workspace's WebSocket endpoint, presents its OIDC
client credential, and sends a notification:

```json
{
  "chap": "0.1",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
  "ts": "2026-05-17T09:00:00.012Z",
  "workspace": "wsp_support_triage",
  "from": "agent:triage-bot#v3.2",
  "to":   "service:coordinator@example.org",
  "type": "notification",
  "method": "participant.announce",
  "params": {
    "uri": "agent:triage-bot#v3.2",
    "type": "agent",
    "display_name": "Support Triage Bot",
    "version": "3.2.0",
    "jwks": {
      "keys": [
        { "kty": "OKP", "crv": "Ed25519", "kid": "k-2026-05-17a",
          "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo" }
      ]
    },
    "capabilities": {
      "kinds": ["draft_response", "classify_intent", "extract_entities"],
      "modes": ["shadow", "trial", "production"],
      "max_concurrent": 32,
      "avg_latency_ms": 480,
      "tool_inventory": ["order-lookup", "shipping-status", "knowledge-base"]
    },
    "scopes": [
      "task.accept", "task.complete", "review.request",
      "whisper.ask", "abstain.declare"
    ],
    "mcp_servers": [
      { "uri": "mcp+https://tools.example.org/orders",   "name": "order-lookup" },
      { "uri": "mcp+https://tools.example.org/shipping", "name": "shipping-status" }
    ]
  },
  "evidence": {
    "prev_hash": "sha256:8b1c…d9e0",
    "sig": "ed25519:k-2026-05-17a:V8M2…q0kg=="
  }
}
```

The Coordinator verifies the signature against the JWK in the announce
itself (a chicken-and-egg situation resolved by trust-on-first-use for
the *first* announce, then pinning the key for subsequent messages).
After acceptance, the Coordinator appends the announce to the evidence
chain and updates the workspace's `members[]` list.

> **Note on TOFU.** The first-announce key is trusted only if the
> transport-level identity (OIDC client credentials, mTLS) matches the
> workspace's policy for permitted agents. Subsequent key rotations
> require `participant.rotate_key` signed with the old key.

---

## 1.2 An admin fetches the workspace descriptor

The admin's client sends:

```json
{
  "chap": "0.1",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2B",
  "ts": "2026-05-17T09:00:05.412Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "workspace.describe",
  "params": {},
  "evidence": {
    "prev_hash": "sha256:9c2d…e1f0",
    "sig": "ed25519:alice-2026-05-17:Q3wD…r4HK=="
  }
}
```

The Coordinator returns the descriptor:

```json
{
  "chap": "0.1",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2C",
  "ts": "2026-05-17T09:00:05.418Z",
  "workspace": "wsp_support_triage",
  "from": "service:coordinator@example.org",
  "to":   "human:alice@example.org",
  "type": "response",
  "reply_to": "01HZ9YWQ7K3X8M2V4N6P8R0T2B",
  "result": {
    "id": "wsp_support_triage",
    "name": "Customer support triage",
    "created": "2026-05-01T09:00:00Z",
    "state": "active",
    "mode": "production",
    "mode_ceiling": "production",
    "step_up_window_sec": 300,
    "max_envelope_bytes": 1048576,
    "coordinator": "service:coordinator@example.org",
    "policy_uri": "https://example.org/policies/support-triage.json",
    "policy_hash": "sha256:abcd…1234",
    "members": [
      { "uri": "human:alice@example.org",    "role": "reviewer", "joined": "2026-05-01T09:00:00Z" },
      { "uri": "human:bob@example.org",      "role": "approver", "joined": "2026-05-01T09:00:00Z" },
      { "uri": "agent:triage-bot#v3.2",      "role": "drafter",  "joined": "2026-05-17T09:00:00Z" },
      { "uri": "service:coordinator@example.org", "role": "coordinator", "joined": "2026-05-01T09:00:00Z" }
    ],
    "shadow_observers": [],
    "evidence_head": "sha256:a1b2…c3d4",
    "evidence_count": 14823,
    "permitted_mcp_servers": [
      "mcp+https://tools.example.org/orders",
      "mcp+https://tools.example.org/shipping",
      "mcp+https://tools.example.org/knowledge-base"
    ]
  },
  "evidence": {
    "prev_hash": "sha256:a1b2…c3d4",
    "sig": "ed25519:coord-2026-05:Wm7Q…s5JH=="
  }
}
```

---

## 1.3 The admin fetches the new agent's descriptor

To verify the agent's claimed capabilities and key:

```json
{
  "chap": "0.1",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2D",
  "ts": "2026-05-17T09:00:08.221Z",
  "workspace": "wsp_support_triage",
  "from": "human:alice@example.org",
  "to":   "service:coordinator@example.org",
  "type": "request",
  "method": "participant.describe",
  "params": { "uri": "agent:triage-bot#v3.2" },
  "evidence": {
    "prev_hash": "sha256:b3c4…d5e6",
    "sig": "ed25519:alice-2026-05-17:T8nP…u9LK=="
  }
}
```

The Coordinator returns the descriptor it pinned at announce time, plus
operational metadata it has gathered (last heartbeat, current load):

```json
{
  "chap": "0.1",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2E",
  "ts": "2026-05-17T09:00:08.228Z",
  "workspace": "wsp_support_triage",
  "from": "service:coordinator@example.org",
  "to":   "human:alice@example.org",
  "type": "response",
  "reply_to": "01HZ9YWQ7K3X8M2V4N6P8R0T2D",
  "result": {
    "uri": "agent:triage-bot#v3.2",
    "type": "agent",
    "display_name": "Support Triage Bot",
    "version": "3.2.0",
    "jwks": {
      "keys": [
        { "kty": "OKP", "crv": "Ed25519", "kid": "k-2026-05-17a",
          "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo" }
      ]
    },
    "capabilities": {
      "kinds": ["draft_response", "classify_intent", "extract_entities"],
      "modes": ["shadow", "trial", "production"],
      "max_concurrent": 32,
      "avg_latency_ms": 480,
      "tool_inventory": ["order-lookup", "shipping-status", "knowledge-base"]
    },
    "scopes": [
      "task.accept", "task.complete", "review.request",
      "whisper.ask", "abstain.declare"
    ],
    "metadata": {
      "last_heartbeat": "2026-05-17T09:00:07.500Z",
      "current_load":   0,
      "status":         "ready"
    }
  },
  "evidence": {
    "prev_hash": "sha256:c5d6…e7f8",
    "sig": "ed25519:coord-2026-05:Xn8R…t6KM=="
  }
}
```

---

## What this gives you

After these three exchanges:

- **The agent is discoverable.** Anyone in the workspace can fetch
  its descriptor and decide whether to route work to it.
- **The agent's key is pinned.** Subsequent messages from
  `agent:triage-bot#v3.2` will be verified against the JWK announced
  here. A new key requires `participant.rotate_key` signed with the old.
- **The workspace state is queryable.** Any member can inspect the
  mode, ceiling, members, evidence head, and policy reference.
- **Three evidence entries exist.** The announce, the workspace
  describe response (informational, but recorded), and the participant
  describe response.

Continue with [`02-task-delegation.md`](./02-task-delegation.md) to see
the agent get its first task.
