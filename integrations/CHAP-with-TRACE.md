# CHAP + TRACE

This document shows how a CHAP review decision composes with
[TRACE](https://github.com/agentrust-io/trace-spec), an open specification for signed
records of an agent run: which model ran, on which runtime, under which policy, calling
which tools. The two protocols record different things:

| Concern                                                        | Protocol |
|----------------------------------------------------------------|----------|
| A person approving, rejecting or overriding an agent's draft   | CHAP     |
| What ran when the agent acted on that decision                 | TRACE    |

The composition is a citation. A TRACE Trust Record points at the CHAP decision by its
position in the workspace audit log and the content hash of its envelope. Neither
protocol carries the other's messages, and neither wire format changes.

---

## 1. The reference

TRACE v0.2 section 3.1.2 defines a `references` block and registers
`rel: "approval-outcome"` for an attributable human approval. A `decide.approve` fills it:

```json
{
  "rel": "approval-outcome",
  "id": "audit/9",
  "resolver": "https://chap.example.org/workspaces/wsp_refund_review",
  "digest": "sha256:ef21916beb9cb5671e95f3c08b3f40c0d41cfd0f79184912eb9ca0f78fddb2e4",
  "retention": "P1Y"
}
```

| Field       | Value                                                                    |
|-------------|--------------------------------------------------------------------------|
| `rel`       | `approval-outcome`                                                       |
| `id`        | `audit/<seq>`, the entry's position in the workspace audit log           |
| `resolver`  | The workspace, as a URI                                                  |
| `retention` | Optional retention undertaking, as an ISO 8601 duration                  |
| `digest`    | SHA-256 of the JCS canonicalisation of the decision envelope, `sha256:<hex>` |

The envelope's JSON-RPC `id` is not used, because it is unique only within one client
session. The audit `seq` is unique within the workspace.

---

## 2. Worked example

The approval the reference above points at, as `audit.read` returns it:

```json
{
  "seq": 9,
  "arrived": "2026-09-14T19:24:05.591Z",
  "envelope": {
    "jsonrpc": "2.0",
    "id": "10",
    "method": "decide.approve",
    "params": {
      "workspace": "wsp_refund_review",
      "task_id": "tsk_01M2GP0TJQ34NXBAB10JYF6ZFB",
      "comment": "Within policy.",
      "tags": [],
      "approved_artefact_digest": "sha256:580a9b57ee97ae0d385e362bf4a8cbb1ad569ba934ed0fe2b48a02d0a5ed9653",
      "from": "human:alice@example.org"
    }
  },
  "prev_hash": "sha256:3d5fca6786ed70a2b54602fe9d23fd6d05bc1dd38d4d3fb76c012a6af2ab6817"
}
```

The JCS canonicalisation of `envelope` hashes to the `digest` the Trust Record carries.
With `approved_artefact_digest` present (CEP-001), the decision also names the draft it
settled.

---

## 3. Checking a reference

A relying party holding the Trust Record and read access to the workspace:

1. Verifies the Trust Record. TRACE does not resolve references during verification,
   and an unresolvable reference does not invalidate the record.
2. Reads entry `seq` through `audit.read`. With no entry, it reports the approval as
   unconfirmed.
3. Recomputes the SHA-256 of the JCS canonicalisation of the entry's `envelope` and
   compares it with `digest`.
4. Runs `audit.verify_chain`, or replays `sha256(JCS(envelope) || prev_hash)` itself, and
   compares the result with the chain head.
5. Checks that `method` is `decide.approve`, or `decide.override` where the relying
   party accepts overrides.

---

## 4. Canonicalisation

The CHAP canonicaliser and the `rfc8785` package TRACE uses produce identical bytes on
every envelope in the fixtures below, and on test objects with non-ASCII strings and
non-BMP keys. CHAP refuses non-integer numbers, which rules out the case where JCS
implementations most often disagree. The TRACE fixture test replays the chain with
`rfc8785` alone and reaches the head the Coordinator reported.

---

## 5. What the composition does not establish

- **That the approval was checked before the action ran.** A Trust Record is issued per
  execution. The component that runs the action enforces the approval, for example by
  refusing a tool call until a matching `decide.approve` exists. The record then cites
  the approval it acted under.
- **That the executed action is the approved draft.** `approved_artefact_digest` binds
  the decision to the draft. The same enforcing component compares the call it makes
  with that digest.
- **Who wrote the log**, unless `security-signed/1.0` is enabled. The fixtures run
  without it.

---

## 6. Fixtures

Four Trust Records and two audit logs produced by `chap-coordinator` 0.2.13 are in
[`examples/chap-approval-outcome`](https://github.com/agentrust-io/trace-spec/tree/main/examples/chap-approval-outcome)
in the TRACE repository: a confirmed approval, an approval edited in the log after the
record was issued, a rejection in the approval's place, and a reference with no entry
behind it. The full mapping is in the TRACE
[CHAP review decisions cross-walk](https://github.com/agentrust-io/trace-spec/blob/main/docs/crosswalks/chap-review-decisions.md).

To run the check yourself, the
[CHAP integration](https://github.com/agentrust-io/integrations/tree/main/integrations/chap)
in agentrust-io/integrations builds the reference from an `audit.read` entry, checks a
reference against an exported log, and emits a signed record citing a live approval.
Its CI runs against `chap-coordinator` 0.2.13 and the latest release.
