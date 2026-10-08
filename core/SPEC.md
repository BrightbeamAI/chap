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

A Core-only implementation should fit in **300-500 lines of code**
in a typical language. The reference implementation in
[`../reference/core/`](../reference/core/) is approximately that size.

For everything else, message signing, OIDC binding, structured
review, multi-party deliberation, etc., see the profile documents
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
    "message": "Missing field: kind"
  }
}
```

Notifications (no response expected) use JSON-RPC's standard
notification shape, same as a request, but with `id` omitted.

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
human:alice@example.org
agent:triage-bot#v3.2
service:coordinator@example.org
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

- **Bearer token** (`Authorization: Bearer <opaque>`), the
  simplest option, suitable for trusted-network deployments.
- **mTLS**: when running inside a service mesh.

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
└─ audit_log[]          (accepted envelopes and recorded refusals, in arrival order)
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
              │ created    │──task.update────>│ declined   │
              └────────────┘                  └────────────┘
```

Core's states are `created`, `in_progress`, `completed` and `declined`.
No Core method moves a task out of `completed`; `review.request`
(`review/1.0`) and `control.supersede` (`control/1.0`) can.
`review_requested` is reachable on every workspace: `task.update` accepts
`in_progress` to `review_requested`, and `task.complete` opens a review on a
task that requires one (§4.6). Where `review/1.0` is not advertised the
decision methods are refused with `-32601`, and `task.update` back to
`in_progress` is the way out. `abstained`, `escalated`, `paused`, `cancelled`
and `superseded` exist only when the relevant profile is in use. The full
transition table is
[`../SPECIFICATION.md`](../SPECIFICATION.md#81-lifecycle) §8.1.

### 3.2 Audit log

Every accepted state-changing envelope MUST be appended to the
workspace's audit log in arrival order, with the Coordinator's own
arrival timestamp. Read-only methods, `workspace.describe` and
`audit.read` among the Core seven, MUST NOT be appended: a log that
grew when read would change what the read reports.
Each entry for an accepted call has at minimum:

```json
{
  "seq":      142,
  "envelope": { "...": "the full received envelope" },
  "arrived":  "2026-05-17T09:14:22.300Z"
}
```

A refused call that is a governed attempt MUST be appended too, as a
refusal entry. It holds the call under `request` rather than `envelope`,
with an `outcome` giving the refusal code, so a reader that replays
`envelope` never treats it as a call that took effect. Which refusals are
recorded is set out in [`../SPECIFICATION.md`](../SPECIFICATION.md) §10.1:

```json
{
  "seq":      143,
  "request":  { "...": "the full received envelope" },
  "outcome":  { "status": "refused", "code": -32011 },
  "arrived":  "2026-05-17T09:14:23.100Z"
}
```

A signed request that is a copy of a recorded refusal, in what its sender
signed, MUST be answered with that refusal, and is not evaluated or recorded
again, so a retry is sent as a new request with a new `id`. The rule, and the
order in which a Coordinator checks a call, are in
[`../SPECIFICATION.md`](../SPECIFICATION.md) §10.1.

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
  "result": { "joined": true, "as": "agent:triage-bot" }
}
```

`workspace`, `from` and `type` are required; a missing one is refused with
`-32602`.

### 4.3 `participant.leave`

**Type:** notification or request.

A participant signals it is leaving the workspace. The Coordinator
removes the participant from the members list. In-flight tasks
remain assigned to the leaving participant. `task.route`
(`routing/1.0`) and `handoff.accept` (`handoff/1.0`) reassign a task;
`control.supersede` (`control/1.0`) replaces it with a new task.

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

Change a task's state, optionally with a `progress_note`. Every
`task.update` MUST carry `state`; without it the call is refused with
`-32602`. From `created` a task may move to `in_progress` or `declined`;
from `in_progress` to `in_progress`, `completed`, `declined` or
`review_requested`, with `completed` refused on a task that requires review.
The full set is the `task.update` rows of
[`../SPECIFICATION.md`](../SPECIFICATION.md#81-lifecycle) §8.1. A progress
report repeats `state: "in_progress"`. Each accepted `task.update` is
appended to the audit log.

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

A `task.update` with `state: "declined"` ends the assignment. The task is
not terminal: with `review/1.0` loaded it can go back to a reviewer, or on
to `escalate.raise`.

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
      "body":    "Hi, I checked the carrier tracking…"
    },
    "confidence": "0.91"
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

A task requires review when it was created with `review_required: true`,
or runs in `trial` mode on a workspace that advertises `modes/1.0`. On such
a task `task.complete` holds `output` as the artefact under review, moves
the task to `review_requested` and answers
`{ "state": "review_requested", "review_id": "<task_id>" }`, whatever the
workspace advertises. The review is addressed to the human members other
than the assignee and the completer; with none, the call is refused with
`-32011`. Only a reviewer decision under `review/1.0` then takes the task
to `completed`. See [`../profiles/review.md`](../profiles/review.md) §3.1.

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
    "range":     { "from_seq": 0, "to_seq": 100 },
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
      { "seq": 15, "envelope": { "...": "..." }, "arrived": "2026-05-17T10:02:11.812Z" },
      { "seq": 16, "request": { "...": "..." },
        "outcome": { "status": "refused", "code": -32011 }, "arrived": "2026-05-17T10:02:12.040Z" }
    ],
    "next_seq": 100
  }
}
```

`range.from_seq` is inclusive and `range.to_seq` is exclusive. `next_seq` is
the `to_seq` the call used, or the length of the log when `to_seq` is
omitted.

Filters supported in Core:

| Filter key  | Behaviour                                    |
|-------------|----------------------------------------------|
| `method`    | Only entries whose call has this method.     |
| `from`      | Only entries whose call has this `from`.     |
| `task_id`   | Only entries whose call carries this `task_id` in `params`, and `whisper.answer` entries for a whisper raised on the task. The `task.create` that made the task, and calls that name it under another field, such as `escalate.raise`, are not returned. |
| `outcome`   | `accepted` or `refused`: only accepted calls, or only recorded refusals. Omitted or null, both. Any other value is refused with `-32602`. |

The call an entry records is its `envelope` when the call was accepted and
its `request` when it was refused, and the other filters read it either way.

Implementations MAY support additional filters; clients MUST
gracefully handle responses that ignore unknown filters.

---

## 5. Error codes (MUST)

Core uses the standard JSON-RPC 2.0 error code ranges:

| Code     | Meaning                                                |
|----------|--------------------------------------------------------|
| `-32700` | Parse error (malformed JSON).                          |
| `-32600` | Invalid request: not a JSON-RPC 2.0 call, nested deeper than 64 levels, or larger than `max_envelope_bytes`. |
| `-32601` | Method not found: an unknown method, or one whose owning profile the workspace does not advertise. |
| `-32602` | Invalid params: missing or wrongly typed fields, an unknown workspace or task, a number that is not a safe integer, or a refused transition. |
| `-32603` | Internal error (Coordinator failure).                  |

CHAP-specific codes start at `-32000` and below and are defined by the
profile documents. Some reach Core methods: `-32011` when `from` is not a
member, or when a review-required `task.complete` finds no eligible human
reviewer; `-32063` when the workspace or the assignee is paused; and
`-32040` when `task.create` asks for a mode above `mode_ceiling`. Where a
deployment enforces signatures or an identity binding, Core methods can
return that profile's codes too.

---

## 6. Liveness and timeouts (SHOULD)

A deployment SHOULD time out idle HTTP connections at 30 seconds. During a
long-running task a participant SHOULD send `task.update` with
`state: "in_progress"` at least every 60 seconds; each is recorded. A
Coordinator MAY remove a participant silent for a configured period,
typically 10 minutes; it rejoins with `participant.join`. The reference
coordinators do not track activity.

---

## 7. Profile discovery (MUST)

A Coordinator that supports profiles beyond Core MUST advertise
them in `workspace.describe`'s `profiles` field:

```json
{
  "profiles": [
    "core/1.0",
    "review/1.0",
    "security-signed/1.0",
    "audit-scitt/1.0"
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
indefinitely. Many real deployments, internal-team chatbots,
solo-operator agent farms, structured-task queues, never need
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
8. **Hour 8.** Run the Core vectors of the harness in
   [`../conformance/harness/`](../conformance/harness/) against your
   server with `--core-only`.

That's a weekend. The reference implementation in
[`../reference/core/`](../reference/core/) covers steps 1-7 in
a single short TypeScript file.

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
