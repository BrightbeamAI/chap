# CHAP + A2A

This document specifies how the Collaborative Human-Agent Protocol composes with the
[Agent-to-Agent (A2A) protocol](https://a2a.dev). CHAP and A2A address
disjoint concerns:

| Concern                                                     | Protocol |
|-------------------------------------------------------------|----------|
| Two agents talking across organisational boundaries         | A2A      |
| The shared room, humans and agents collaborating inside one organisation | CHAP      |

The composition pattern is **bridge service**, not federation. Inside
each organisation's CHAP workspace, a single Participant of type
`service` represents the cross-boundary peer. That service participant
speaks CHAP locally and A2A remotely. Each workspace's evidence chain
remains authoritative within its own organisation; cross-organisation
evidence is joined via the bridge's citations.

This pattern keeps trust boundaries explicit, audits local, and
keeps both protocols simple.

---

## 1. The boundary

```
┌─── CHAP Workspace A (org A) ────┐         ┌─── CHAP Workspace B (org B) ────┐
│                                │         │                                │
│  human ─── CHAP ──> coordinator │         │  coordinator <── CHAP ─── human │
│                       │        │         │      │                         │
│                       ▼        │         │      ▼                         │
│              bridge:partner    │ ─ A2A ─ │  bridge:partner                │
│              (service)         │         │  (service)                     │
│                                │         │                                │
└────────────────────────────────┘         └────────────────────────────────┘
```

A request to "delegate this task to a peer at another organisation":

1. The local human or agent emits a normal `task.create` to
   `service:bridge@example.org`.
2. The bridge takes the task in progress, translates it into an A2A
   request, and sends it over A2A to the peer organisation.
3. The peer organisation does whatever it does, internally, that
   workspace handles the request via its own CHAP flow.
4. The peer returns an A2A response. The local bridge ingests it,
   produces a CHAP artefact citing the A2A correlation, and emits
   `task.complete` locally.

From the perspective of every other Participant in the local
workspace, the bridge is just a service agent that happens to be
slower than usual. The cross-organisation hop is encapsulated.

---

## 2. The bridge participant

The bridge is a Participant of type `service`. Its descriptor declares
A2A as part of its capabilities:

```json
{
  "uri": "service:bridge-to-partner-a@example.org",
  "type": "service",
  "display_name": "A2A bridge to Partner A",
  "jwks": { "keys": [ /* … */ ] },
  "capabilities": {
    "kinds": ["a2a_delegation"],
    "modes": ["trial", "production"],
    "max_concurrent": 16,
    "avg_latency_ms": 2400
  },
  "scopes": ["task.update", "task.complete", "review.request"],
  "metadata": {
    "a2a": {
      "peer_endpoint":   "https://a2a.partner-a.example.com/v1",
      "peer_identity":   "agent:partner-a-ops@partner-a.example.com",
      "auth_method":     "mtls",
      "permitted_kinds": ["data_lookup", "content_review", "translation"]
    }
  }
}
```

The bridge's permitted operations are described in the workspace's
policy. The workspace's `permitted_a2a_peers` list constrains which
remote endpoints the bridge may contact.

---

## 3. Worked example

A document-translation team uses an external partner for languages
its in-house team doesn't cover. A human delegates a translation
task to the bridge; the bridge moves the work over A2A; the result
returns as a CHAP artefact.

### 3.1 Local task.create

```json
{
  "chap": "0.2",
  "id": "01HZBF1R0K3X8M2V4N6P8R0TCA",
  "ts": "2026-05-17T16:45:00.000Z",
  "workspace": "wsp_translation_intake",
  "from": "human:liam@example.org",
  "to":   "service:bridge-to-partner-a@example.org",
  "type": "request",
  "method": "task.create",
  "params": {
    "workspace": "wsp_translation_intake",
    "from": "human:liam@example.org",
    "kind": "a2a_delegation",
    "mode": "production",
    "assignee": "service:bridge-to-partner-a@example.org",
    "input": {
      "remote_kind": "translation",
      "source_lang": "en",
      "target_lang": "ko",
      "document_uri": "https://example.org/docs/quarterly-report.pdf",
      "purpose": "internal-circulation",
      "deadline": "2026-05-19T17:00:00Z",
      "max_a2a_calls": 1
    },
    "deadline": "2026-05-19T17:00:00Z",
    "review_required": true
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:liam-2026-05-17:…" }
}
```

The Coordinator answers with the minted `task_id`
(`tsk_01HZBF1R0K3X8M2V4N6P8R0TCB`) in state `created`.

### 3.2 Bridge takes the task (locally) and sends (remotely)

```json
{
  "chap": "0.2",
  "id": "01HZBF1R0K3X8M2V4N6P8R0TCC",
  "ts": "2026-05-17T16:45:00.412Z",
  "workspace": "wsp_translation_intake",
  "from": "service:bridge-to-partner-a@example.org",
  "to":   "human:liam@example.org",
  "type": "request",
  "method": "task.update",
  "params": {
    "workspace": "wsp_translation_intake",
    "from": "service:bridge-to-partner-a@example.org",
    "task_id": "tsk_01HZBF1R0K3X8M2V4N6P8R0TCB",
    "state": "in_progress",
    "progress_note": "Queued for the partner; round trip expected within two hours."
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:bridge-…:…" }
}
```

The bridge immediately makes an A2A request to the partner. The A2A
specifics are out of scope here; the important detail is the
**correlation_id** the bridge mints for cross-protocol linkage:

```
A2A request:
  X-A2A-Correlation-Id: a2a_01HZBF1R0K3X8M2V4N6P8R0TCD
  body: { task: 'translation', source: 'en', target: 'ko', doc_uri: '…', deadline: '…' }
```

The bridge optionally emits a further CHAP `task.update` notification
carrying the correlation id, so local observers see that the bridge is
in flight:

```json
{
  "method": "task.update",
  "params": {
    "workspace": "wsp_translation_intake",
    "from": "service:bridge-to-partner-a@example.org",
    "task_id": "tsk_01HZBF1R0K3X8M2V4N6P8R0TCB",
    "state": "in_progress",
    "progress_note": "a2a_request_sent; correlation_id=a2a_01HZBF1R0K3X8M2V4N6P8R0TCD; peer=agent:partner-a-ops@partner-a.example.com"
  }
}
```

### 3.3 Bridge receives A2A response, completes locally

```json
{
  "chap": "0.2",
  "id": "01HZBG2S1K3X8M2V4N6P8R0TCE",
  "ts": "2026-05-17T18:32:14.001Z",
  "workspace": "wsp_translation_intake",
  "from": "service:bridge-to-partner-a@example.org",
  "to":   "human:liam@example.org",
  "type": "request",
  "method": "task.complete",
  "params": {
    "task_id": "tsk_01HZBF1R0K3X8M2V4N6P8R0TCB",
    "artefact": {
      "id": "art_01HZBG2S1K3X8M2V4N6P8R0TCF",
      "kind": "draft",
      "produced_by": "service:bridge-to-partner-a@example.org",
      "produced_at": "2026-05-17T18:32:13.512Z",
      "task": "tsk_01HZBF1R0K3X8M2V4N6P8R0TCB",
      "content": {
        "translation_uri": "https://example.org/docs/quarterly-report.ko.pdf",
        "translator_attestation": "Partner A   translation team",
        "quality_score": "4.6"
      },
      "citations": [
        {
          "kind": "a2a_correlation",
          "peer": "agent:partner-a-ops@partner-a.example.com",
          "correlation_id": "a2a_01HZBF1R0K3X8M2V4N6P8R0TCD",
          "input_hash":  "sha256:c4d5e607182930415263748596071829304152637485960718293041526374a4",
          "output_hash": "sha256:d5e607182930415263748596071829304152637485960718293041526374a4b5"
        }
      ],
      "content_hash": "sha256:e607182930415263748596071829304152637485960718293041526374a4b5c6"
    }
  },
  "evidence": { "prev_hash": "sha256:…", "sig": "ed25519:bridge-…:…" }
}
```

Liam reviews and approves like any other task. The fact that work
crossed an organisational boundary is visible (the artefact's
`citations` carry the A2A correlation) but does not change the local
control flow.

---

## 4. Trust and identity

The bridge is the trust anchor. Specifically:

- **Locally**, the bridge signs CHAP messages with its own key,
  registered in the local workspace. Local Participants trust the
  bridge to the same extent they would trust any service agent in
  the workspace.
- **Remotely**, the bridge authenticates to the A2A peer via the
  peer's required mechanism (mTLS, OIDC client credentials, etc.).
  The A2A protocol's own audit trail covers the cross-boundary leg.
- The bridge **does not relay** the peer's signature into the local
  chain. The local chain attests "the bridge says this came from the
  peer." Cross-organisation non-repudiation requires the bridge's
  own attestation plus the A2A peer's own audit log.

This is the most common workable trust model in practice. Stronger
guarantees (e.g. relaying the peer's signature) require the peer's
key to be addressable inside the local workspace, which couples the
organisations more tightly than most deployments want.

---

## 5. Validation

A conformant Coordinator that hosts a bridge SHOULD:

1. Reject any `task.create` to the bridge whose `params.input` refers
   to a remote endpoint not in the workspace's
   `permitted_a2a_peers` list. Error `-32500` (`policy_denied`).
2. Enforce the bridge's per-task `max_a2a_calls` budget if declared.
3. Record A2A failures as `task.update` notifications whose progress
   note carries the `a2a_request_failed` stage, followed by either a
   retry, a completion citing the failure, or an `abstain.declare`.

---

## 6. Failure modes

| Scenario                                          | Recommended handling                                            |
|---------------------------------------------------|-----------------------------------------------------------------|
| A2A peer unreachable                              | Retry per policy; if exhausted, `abstain.declare` with category `peer_unreachable`. |
| A2A peer responds with error                      | Emit `task.update` with the error summary; complete with citation including the error hash, or abstain depending on workspace policy. |
| A2A response received but doesn't match expected schema | Treat as a failure; do not produce an artefact with bad data. |
| Local workspace closes during in-flight A2A call  | Bridge completes the A2A round-trip but emits the result into a `closed` workspace as a notification; admin replays if needed. |

---

## 7. Recap

| Question                                                            | Answer                                |
|---------------------------------------------------------------------|---------------------------------------|
| Do A2A messages cross the CHAP wire?                                 | No.                                   |
| What represents the remote peer locally?                            | A `service:bridge…` participant.      |
| Where does cross-organisation evidence go?                          | Into CHAP citations + the A2A protocol's own audit log. |
| Can a local-only auditor see what happened across the boundary?     | They see *that* it happened, with hashes; bodies require A2A audit access. |
| Do both workspaces share an evidence chain?                         | No. Each workspace's chain is local.  |
| What does the local chain commit about the remote work?             | The correlation_id, the input/output hashes, and the bridge's signature. |

For the wire-level details, see [`SPECIFICATION.md`](../SPECIFICATION.md)
§16.2.

---

## 8. Code

The patterns above describe the **semantic** integration: how an A2A
exchange shows up in a CHAP audit trail. CHAP ships a
**transport** integration that goes the other way too: a CHAP
Coordinator can present itself **as** an A2A agent, so any
A2A-aware orchestrator (Azure AI Foundry, Amazon Bedrock AgentCore,
Google ADK, custom multi-agent systems) can register it by URL,
discover its skills, and delegate work to it.

### What ships

- `packages/coordinator-a2a/`. TypeScript adapter (`@brightbeamai/chap-coordinator-a2a`),
  on the official `@a2a-js/sdk` 1.x (A2A spec **1.0**), and `chapA2aGuard`,
  the checks a server on the user's machine makes before its routes.
- `chap_coordinator.transports.a2a_server`. Python adapter, installable
  via `pip install chap-coordinator[a2a]`, on the official `a2a-sdk` 1.x
  (A2A spec **1.0**). `chap_coordinator.transports.a2a_app` builds the
  server as an ASGI app behind the same checks, and
  `python -m chap_coordinator.transports.a2a_cli` serves it.
- `reference/a2a-server-ts/` and `reference/a2a-server-py/`. Runnable
  HTTP servers using each language's idiomatic web framework
  (Express, Starlette).
- Inward wrap helpers in both languages (`wrap_a2a_message_exchange`,
  `wrapA2aMessageExchange`) implement the bridge-participant pattern
  in §3 as a library utility.

### A2A 1.0, and 0.3 clients

Both adapters implement **A2A 1.0**: JSON-RPC methods in PascalCase
(`SendMessage`, `GetTask`), parts as `{data, mediaType}`, and task states
named `TASK_STATE_*`. Both cards list the JSON-RPC endpoint twice, at
protocol version 1.0 and at 0.3, and both servers turn on their SDK's v0.3
compatibility (`legacyCompat` in TypeScript, `enable_v0_3_compat` in
Python), so a client still on A2A 0.3 reads the card in its 0.3 shape and
calls `message/send` at the same URL. A server that leaves the
compatibility off builds its card with `legacyCompat: false`
(`legacy_compat=False`), so the card lists the 1.0 interface alone. Every
input and output mode is `application/json`.

### What gets exposed

Each CHAP method the server offers appears as an `AgentSkill` on the Agent
Card with id `chap.<method>`. The skill names match the MCP adapter's tool
names, so callers fluent in one are fluent in the other:

```
chap.workspace.create        chap.review.request
chap.workspace.describe      chap.decide.approve
chap.participant.join        chap.decide.reject
chap.task.create             chap.decide.override
...
```

(Every method is listed in `CHAP-with-MCP.md` §10.)

By default the card lists, and the executor runs, the agent's skills: the
`AGENT_MCP_TOOLS` set the MCP commands offer an assistant, which covers the
agent's own work and leaves out workspace, membership and control methods
and the choice of who reviews. `skillFilter: () => true` (`skill_filter=
lambda _name: True`) on both offers every method, and so does `--skills
all` on the reference servers and the Python command.

A2A messages carry the CHAP params in a data part. The skill id
identifies which CHAP method to dispatch, looked up in this order:
`message.metadata.skill` first, then `skill` in the first data part.
Either is acceptable; orchestrators typically populate the metadata path.

An answer is an agent message whose data part is the call's result. A
call CHAP refuses is a failure in A2A's terms: a task in
`TASK_STATE_REJECTED`, or `TASK_STATE_FAILED` when the coordinator could
not complete it, whose status message carries the CHAP error as
`{chap_error, message, data?}`. An orchestrator that reads only A2A
states sees the failure; one that reads CHAP codes has them. A refusal is
final the moment it is made, so the servers keep refusals in a
`ChapTaskStore`: the 1,000 most recent, each without the request it
refused, which GetTask still answers for and memory cannot grow past.

### Who the server acts as

An A2A message carries no signature, so an executor that lets each call
name its own `from` lets any caller act as anyone, a person included.
Both executors take `participant`, as the MCP servers do: `from` is filled
in on every call, and a call naming anyone else is refused with `-32011`.
The reference servers and the Python command act as
`agent:orchestrator@local` unless `CHAP_PARTICIPANT`, or `--as` for the
TypeScript server and the Python command, names another, and refuse a
`human:` participant: a person decides in a desk, never through an
orchestrator. The libraries refuse a `human:` participant too. They offer
the agent's skills unless `--skills all` is given, so by default the
orchestrator cannot open workspaces, change who is in them or request
reviews; `skillFilter` (`skill_filter=`) sets the skills, on the card and
in the executor alike.

### What a local server checks

A browser sends a request to a loopback address from any page, so an A2A
server on the user's machine makes the create-chap-app servers' checks
before its routes: a `Host` that is not one of its names is refused with
421, which defeats DNS rebinding; an `Origin` naming another site with 403;
a POST that is not `application/json` with 415, which stops a page's
cross-site `fetch` with a `text/plain` body; a body over 1,114,112 bytes
with 413 before the rest is read; and a body that nests deeper than 32
levels with 400, before a parser deeper in runs out of stack. Each refusal
is a short JSON body.
A fault inside the coordinator or the server reaches the client as a
failure without its detail, which stays in the server's log, so no stack
trace, path or exception text crosses the wire; a malformed A2A request is
answered with the SDK's own account of what is wrong with it.

None of this authenticates a caller: any local process can call the server,
as its one participant. A2A's security schemes, declared on the card and
enforced at the transport, are how a deployment names its callers.

### Quickstart

Start either reference server, or the Python command, then point an A2A
client at its agent-card URL:

```bash
# TypeScript, from reference/a2a-server-ts in a clone, after npm install and npm run build at its root
npm start -- --port 9090
# Python
python3 reference/a2a-server-py/server.py --port 9090
python -m chap_coordinator.transports.a2a_cli --port 9090 --db ~/chap-a2a.db
```

Each offers the agent's skills; add `--skills all` for every method.

```bash
curl http://localhost:9090/.well-known/agent-card.json
```

A worked walkthrough is at
[`examples/drive-chap-from-an-a2a-orchestrator.md`](../examples/drive-chap-from-an-a2a-orchestrator.md).

### Composition

The transport adapter and the bridge-participant pattern are
complementary, not exclusive. An orchestrator can drive a CHAP
workspace via the adapter (this section), and that workspace can in
turn cite A2A exchanges it makes with *other* peers via the
citation pattern in §2 above. Same A2A wire format underneath, two
different roles for it.
