# CHAP Core Specification

**Audience:** Implementers · **Profile id:** `core/1.0`

This document is the **minimum** specification for a CHAP-compatible
participant or coordinator. It defines:

- 7 methods, all required.
- A JSON-RPC 2.0 wire format with CHAP-specific extension fields.
- An audit log requirement (in-memory or DB is fine; cryptographic
  audit is a separate optional profile).
- No required cryptography. No required identity provider. No
  required external services.

A Core-only implementation should fit in **300–500 lines of code**
in a typical language. The reference implementation in
[`../reference/core/`](../reference/core/) is approximately that size.

For everything else — message signing, OIDC binding, structured
review, multi-party deliberation, etc. — see the profile documents
in [`../profiles/`](../profiles/).

---

## 1. Conformance language

The keywords MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY in this
document are to be interpreted as in [RFC 2119](https://datatracker.ietf.org/doc/html/rfc2119)
and [RFC 8174](https://datatracker.ietf.org/doc/html/rfc8174).

A Core-conformant implementation MUST implement every section
marked with **(MUST)**. Sections marked **(SHOULD)** describe
strong recommendations whose absence may impair interoperability.
Sections marked **(MAY)** are optional.

---

## 2. The wire (MUST)

### 2.1 Envelope

Every CHAP message is a single JSON object that is **also** a valid
[JSON-RPC 2.0](https://www.jsonrpc.org/specification) message:

```json
{
  "jsonrpc": "2.0",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
  "method": "task.create",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "agent:triage-bot",
    "ts":        "2026-05-17T09:14:22.184Z",
    "kind":      "draft_response",
    "input":     { "ticket_id": "INC-48219" }
  }
}
```

JSON-RPC's `id` field doubles as CHAP's message id. The `method`
field is one of the 7 Core methods listed in §4. All CHAP-specific
fields live inside `params`.

For responses:

```json
{
  "jsonrpc": "2.0",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
  "result": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "state":   "created"
  }
}
```

For errors, the standard JSON-RPC error shape applies:

```json
{
  "jsonrpc": "2.0",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
  "error": {
    "code":    -32602,
    "message": "Invalid params: missing 'kind'",
    "data":    { "field": "kind" }
  }
}
```

Notifications (no response expected) use JSON-RPC's standard
notification shape — same as a request, but with `id` omitted.

### 2.2 Required CHAP fields inside `params`

Every CHAP method's `params` MUST include:

| Field        | Type     | Description                                            |
|--------------|----------|--------------------------------------------------------|
| `workspace`  | string   | Workspace identifier this message belongs to.          |
| `from`       | string   | The sender's Participant URI.                          |
| `to`         | string · or array of strings | The intended recipient(s).         |
| `ts`         | string   | Sender's timestamp in RFC 3339 with milliseconds.      |

Method-specific fields are documented in §4.

### 2.3 Identifiers

- Message `id`: any string that is unique to the sender within a
  reasonable de-duplication window. ULIDs (Crockford base32, 26
  chars) are RECOMMENDED but not required.
- Workspace id: `wsp_` + URL-safe alphanumeric.
- Task id: `tsk_` + URL-safe alphanumeric.
- Participant URI: one of `human:`, `agent:`, `service:`, `group:`,
  `workspace:` followed by a local identifier and optional
  `@authority` (DNS-style) and `#version` suffix. Examples:

```
human:[email protected]
agent:triage-bot#v3.2
service:[email protected]
group:on-call@example.org
workspace:wsp_demo
```

### 2.4 Transport (MUST)

A Core implementation MUST accept CHAP envelopes over **HTTP POST**
to a fixed path (`/chap` is recommended). The request body is a
single envelope; the response body is the corresponding response
envelope.

Implementations MAY additionally support WebSocket, HTTP+SSE,
NATS, Kafka, or any other transport. The wire format is identical
across transports.

TLS is REQUIRED for production deployments. Plain HTTP is permitted
for local development only.

### 2.5 Authentication (SHOULD)

A Core implementation SHOULD authenticate requests via one of:

- **Bearer token** (`Authorization: Bearer <opaque>`) — the
  simplest option, suitable for trusted-network deployments.
- **mTLS** — when running inside a service mesh.

Both options leave the *binding* of credentials to Participant URIs
to a deployment-specific mapping. Cryptographic per-message
signatures are a separate profile (`security-signed`).

A Core implementation MAY accept unauthenticated requests on
loopback for local development.

---

## 3. State model (MUST)

A Coordinator (or a peer participant acting as its own Coordinator)
maintains the following state per workspace:

```
Workspace
├─ id, created, state
├─ members[]            (Participant URIs + roles)
├─ tasks[]
│  └─ Task { id, state, kind, assignee, input, output?, history }
├─ messages[]           (free-form chat / notifications)
└─ audit_log[]          (every accepted envelope in arrival order)
```

The state may live in memory for testing or in any durable store
(SQLite, Postgres, etc.) for production. Core has no opinion.

### 3.1 Task states

A Task is a finite-state machine over these states:

```
              ┌────────────┐                  ┌────────────┐
─task.create──> │ created  │──task.update────>│ in_progress│──┐
              └────────────┘                  └────────────┘  │
                                                              │
                                                  task.complete (terminal)
                                                              │
                                                              ▼
                                                       ┌────────────┐
                                                       │ completed  │
                                                       └────────────┘
              ┌────────────┐                  ┌────────────┐
              │ created    │──task.update────>│ declined   │  (terminal)
              └────────────┘                  └────────────┘
```

The three states are: `created`, `in_progress`, `completed` (with
`declined` as a terminal alternative). Richer states — `review_requested`,
`abstained`, `escalated`, `superseded`, `cancelled` — exist only
when the relevant profile is in use.

### 3.2 Audit log

Every accepted envelope MUST be appended to the workspace's audit
log in arrival order, with the Coordinator's own arrival timestamp.
Each log entry has at minimum:

```json
{
  "seq":      142,
  "envelope": { "...": "the full received envelope" },
  "arrived":  "2026-05-17T09:14:22.300Z"
}
```

The Coordinator MUST be able to return ranges of the log via
`audit.read` (§4.7). There is **no cryptographic chaining
requirement at this layer**. Cryptographic audit is the `audit-scitt`
profile.

---

## 4. The 7 Core methods (MUST)

Every Core-conformant implementation MUST implement all seven.

### 4.1 `workspace.describe`

**Type:** request · **Returns:** workspace descriptor.

Returns the current state of the workspace.

```json
{
  "jsonrpc": "2.0",
  "id": "01HZ…2",
  "method": "workspace.describe",
  "params": {
    "workspace": "wsp_demo",
    "from": "human:alice@example.org",
    "to":   "service:coordinator@example.org",
    "ts":   "2026-05-17T09:00:00Z"
  }
}
```

Response:

```json
{
  "jsonrpc": "2.0",
  "id": "01HZ…2",
  "result": {
    "id":      "wsp_demo",
    "created": "2026-05-01T09:00:00Z",
    "state":   "active",
    "members": [
      { "uri": "human:alice@example.org",   "role": "reviewer", "joined": "2026-05-01T09:00:00Z" },
      { "uri": "agent:triage-bot",          "role": "drafter",  "joined": "2026-05-17T09:00:00Z" }
    ],
    "profiles": ["core/1.0"],
    "audit_count": 142
  }
}
```

The `profiles` field lists every profile this workspace supports.
Core-only deployments report `["core/1.0"]`.

### 4.2 `participant.join`

**Type:** request · **Returns:** join confirmation.

A new participant announces itself.

```json
{
  "method": "participant.join",
  "params": {
    "workspace":   "wsp_demo",
    "from":        "agent:triage-bot",
    "to":          "service:coordinator@example.org",
    "ts":          "2026-05-17T09:00:00Z",
    "type":        "agent",
    "display_name": "Triage Bot v3.2",
    "role":        "drafter",
    "capabilities": { "kinds": ["draft_response"] }
  }
}
```

Response:

```json
{
  "result": { "joined": true, "as": "agent:triage-bot", "role": "drafter" }
}
```

### 4.3 `participant.leave`

**Type:** notification or request.

A participant signals it is leaving the workspace. The Coordinator
removes the participant from the members list. In-flight tasks
remain assigned to the leaving participant unless explicitly
reassigned by an administrator (see Control profile).

```json
{
  "method": "participant.leave",
  "params": {
    "workspace": "wsp_demo",
    "from":      "agent:triage-bot",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T17:00:00Z",
    "reason":    "shutdown_for_upgrade"
  }
}
```

### 4.4 `task.create`

**Type:** request · **Returns:** created task.

Create a new task and assign it to a participant.

```json
{
  "method": "task.create",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "agent:triage-bot",
    "ts":        "2026-05-17T09:14:22.184Z",
    "kind":      "draft_response",
    "assignee":  "agent:triage-bot",
    "input":     { "ticket_id": "INC-48219", "customer_message": "…" },
    "deadline":  "2026-05-17T09:30:00Z"
  }
}
```

Response:

```json
{
  "result": {
    "task_id": "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "state":   "created"
  }
}
```

The Coordinator MUST validate that the assignee is a current
workspace member.

### 4.5 `task.update`

**Type:** notification or request.

Report progress on a task or change its state from `created` to
`in_progress`. Multiple `task.update` messages per task are
permitted.

```json
{
  "method": "task.update",
  "params": {
    "workspace":     "wsp_demo",
    "from":          "agent:triage-bot",
    "to":            "human:alice@example.org",
    "ts":            "2026-05-17T09:14:23.000Z",
    "task_id":       "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "state":         "in_progress",
    "progress_note": "Starting; calling order-lookup tool."
  }
}
```

A `task.update` with `state: "declined"` is a terminal transition;
the task does not progress further.

### 4.6 `task.complete`

**Type:** request · **Returns:** completion acknowledgement.

Mark a task as completed and deliver its output.

```json
{
  "method": "task.complete",
  "params": {
    "workspace": "wsp_demo",
    "from":      "agent:triage-bot",
    "to":        "human:alice@example.org",
    "ts":        "2026-05-17T09:14:27.012Z",
    "task_id":   "tsk_01HZ9YX7K3X8M2V4N6P8R0T3B",
    "output": {
      "subject": "Re: order ORD-91204 delivery delay",
      "body":    "Hi — I checked the carrier tracking…"
    },
    "confidence": 0.91
  }
}
```

Response:

```json
{ "result": { "state": "completed" } }
```

This terminal transition closes the task. Any further `task.update`
or `task.complete` for the same task_id MUST be rejected with
`-32602` (`Invalid params`).

A Core-only deployment treats `task.complete` as the end of the
flow. With the `review` profile (see [`../profiles/review.md`](../profiles/review.md)),
completion may instead trigger a review request.

### 4.7 `audit.read`

**Type:** request · **Returns:** audit log entries.

Read a range of the workspace's audit log.

```json
{
  "method": "audit.read",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T17:30:00Z",
    "range":     { "from_seq": 0, "to_seq": 200 },
    "filter":    { "method": "task.complete" }
  }
}
```

Response:

```json
{
  "result": {
    "entries": [
      { "seq": 7,  "envelope": { "...": "..." }, "arrived": "2026-05-17T09:14:27.300Z" },
      { "seq": 15, "envelope": { "...": "..." }, "arrived": "2026-05-17T10:02:11.812Z" }
    ],
    "next_seq": 201
  }
}
```

Filters supported in Core:

| Filter key  | Behaviour                                    |
|-------------|----------------------------------------------|
| `method`    | Only entries whose envelope method matches.  |
| `from`      | Only entries whose envelope `from` matches.  |
| `task_id`   | Only entries referencing this task id.       |
| `ts_range`  | Only entries within the time window.         |

Implementations MAY support additional filters; clients MUST
gracefully handle responses that ignore unknown filters.

---

## 5. Error codes (MUST)

Core uses the standard JSON-RPC 2.0 error code ranges:

| Code     | Meaning                                                |
|----------|--------------------------------------------------------|
| `-32700` | Parse error (malformed JSON).                          |
| `-32600` | Invalid request (not a valid JSON-RPC message).        |
| `-32601` | Method not found (unknown CHAP method).                 |
| `-32602` | Invalid params (missing or wrongly-typed fields).      |
| `-32603` | Internal error (Coordinator failure).                  |

CHAP-specific codes (used by profiles) start at `-32000` and below;
Core does not define any. See individual profile docs.

---

## 6. Liveness and timeouts (SHOULD)

A Core implementation SHOULD:

- Time out idle HTTP connections at 30 seconds.
- Send a `task.update` heartbeat at least every 60 seconds during
  long-running tasks.
- Drop participants that have not sent any message for 10 minutes
  (configurable). A dropped participant must call `participant.join`
  again to re-enter the workspace.

These thresholds are configurable; the values above are
recommendations for typical deployments.

---

## 7. Profile discovery (MUST)

A Coordinator that supports profiles beyond Core MUST advertise
them in `workspace.describe`'s `profiles` field:

```json
{
  "profiles": [
    "core/1.0",
    "review/0.1",
    "security-signed/0.1",
    "audit-scitt/0.1"
  ]
}
```

Each profile string is `<name>/<version>`. Clients use this to
decide which methods are available.

A profile MAY define additional fields, methods, error codes, and
state-machine transitions. Profiles MUST NOT redefine Core methods
in incompatible ways. Profiles MAY tighten what Core leaves
optional.

---

## 8. What Core does NOT include

To make the boundary explicit:

| Feature                              | Where to find it                                    |
|--------------------------------------|-----------------------------------------------------|
| Cryptographic message signing        | [`../profiles/security-signed.md`](../profiles/security-signed.md) |
| Hash-chained / SCITT audit log       | [`../profiles/audit-scitt.md`](../profiles/audit-scitt.md)        |
| OIDC identity binding                | [`../profiles/identity-oidc.md`](../profiles/identity-oidc.md)    |
| W3C VC identity binding              | [`../profiles/identity-vc.md`](../profiles/identity-vc.md)        |
| Review / approve / override workflow | [`../profiles/review.md`](../profiles/review.md)                   |
| Whisper (interrupt-style questions)  | [`../profiles/whisper.md`](../profiles/whisper.md)                 |
| Multi-party deliberation             | [`../profiles/deliberation.md`](../profiles/deliberation.md)       |
| Shadow / Trial / Production modes    | [`../profiles/modes.md`](../profiles/modes.md)                     |
| Handoff between participants         | [`../profiles/handoff.md`](../profiles/handoff.md)                 |
| Pause / resume / snapshot / rollback | [`../profiles/control.md`](../profiles/control.md)                 |
| MCP tool-call citations              | [`../integrations/CHAP-with-MCP.md`](../integrations/CHAP-with-MCP.md) |
| A2A cross-org delegation             | [`../integrations/CHAP-with-A2A.md`](../integrations/CHAP-with-A2A.md) |

A workspace that needs none of these can operate at Core level
indefinitely. Many real deployments — internal-team chatbots,
solo-operator agent farms, structured-task queues — never need
more than Core.

---

## 9. Implementing Core in a weekend

A practical sequence:

1. **Hour 1.** Set up an HTTP server that accepts POST to `/chap`,
   parses JSON-RPC 2.0, dispatches by `method`.
2. **Hour 2.** Implement `workspace.describe` and an in-memory
   workspace state with members.
3. **Hour 3.** Implement `participant.join` and `participant.leave`.
4. **Hour 4.** Implement `task.create`, `task.update`, `task.complete`
   with the state machine.
5. **Hour 5.** Implement the in-memory audit log and `audit.read`
   with filter and range support.
6. **Hour 6.** Implement the five JSON-RPC error codes and graceful
   handling of malformed requests.
7. **Hour 7.** Write a tiny client that walks through the
   end-to-end demo: workspace.describe → participant.join (agent) →
   task.create → task.update → task.complete → audit.read.
8. **Hour 8.** Run the conformance vectors in
   [`../conformance/test-vectors.md`](../conformance/test-vectors.md)
   for the Core subset.

That's a weekend. The reference implementation in
[`../reference/core/`](../reference/core/) covers steps 1–7 in
about 300 lines of TypeScript.

---

## 10. Going further

Once Core works:

1. Add the **`review`** profile if your workflow involves humans
   approving agent output. This is where CHAP's structured-override
   superpower lives.
2. Add **`security-signed`** if you need non-repudiation or
   cross-trust-boundary audit.
3. Add **`audit-scitt`** if you need cryptographic audit and you're
   willing to run a SCITT transparency service.
4. Add **`identity-oidc`** or **`identity-vc`** if you need
   verified identity beyond bearer tokens.
5. Compose with **MCP** if your agents call tools.
6. Add other profiles as workflows require.

Each profile is independent. You don't pay for what you don't use.
