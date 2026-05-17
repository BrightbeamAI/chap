# HAP Conformance Checklist

This is the self-attestation template for HAP. An
implementation claims conformance by:

1. Filling in the **Core** checklist below.
2. Filling in a **profile checklist** for each profile it implements.
3. Publishing the completed attestation as an
   [in-toto attestation](https://github.com/in-toto/attestation) with
   subject `hap-implementation:<name>:<version>` and predicate
   `hap.dev/conformance/v1`.

Conformance is **Core + the set of profiles attested**. There is no
single "overall" conformance level; implementations announce
exactly which profiles they support and at what version.

A workspace's `workspace.describe` MUST advertise the same profile
list that its attestation claims.

---

## Implementation identity

| Field                  | Value |
|------------------------|-------|
| Implementation name    |       |
| Version                |       |
| Vendor / author        |       |
| Repository / homepage  |       |
| Date of attestation    |       |
| Attestation signed by  |       |
| Profiles claimed       | `core/1.0` + (list profile versions, e.g. `review/1.0, modes/1.0`) |

---

## Core conformance (mandatory)

An implementation MUST satisfy every item below to claim any
HAP conformance.

### C1 · Wire format

- [ ] Envelopes are valid [JSON-RPC 2.0](https://www.jsonrpc.org/specification) requests, responses, or notifications.
- [ ] Required HAP fields (`workspace`, `from`, `to`, `ts`) are present inside `params`.
- [ ] `ts` is RFC 3339 with millisecond precision.
- [ ] Participant URIs match the grammar in [`../SPECIFICATION.md`](../SPECIFICATION.md) §18.

### C2 · Transport

- [ ] Accepts HAP envelopes over HTTP POST to a documented path (`/hap` recommended).
- [ ] Uses TLS in production deployments.
- [ ] Returns the corresponding response envelope in the HTTP response body.

### C3 · The 7 methods

- [ ] `workspace.describe` returns `id`, `created`, `state`, `members`, `profiles`, `audit_count`.
- [ ] `participant.join` adds the participant to `members`; rejects with `-32602` for missing fields.
- [ ] `participant.leave` removes the participant; idempotent.
- [ ] `task.create` validates that the assignee is a current member; returns `task_id` and `state: "created"`.
- [ ] `task.update` enforces the `created → in_progress → (completed|declined)` state machine; rejects illegal transitions with `-32602`.
- [ ] `task.complete` is terminal; subsequent transitions on the same `task_id` are rejected with `-32602`.
- [ ] `audit.read` supports `range` and at minimum the `method`, `from`, `task_id` filters; returns `entries` and `next_seq`.

### C4 · Audit log

- [ ] Every accepted envelope is appended in arrival order.
- [ ] Each entry records the Coordinator's arrival timestamp.
- [ ] `audit.read` results are stable: the same range returns the same entries indefinitely.

### C5 · Error handling

- [ ] Returns `-32700` for malformed JSON.
- [ ] Returns `-32600` for non-JSON-RPC-2.0 requests.
- [ ] Returns `-32601` for unknown methods.
- [ ] Returns `-32602` for missing or wrongly-typed parameters.
- [ ] Returns `-32603` for internal failures, with no leaked stack traces.

### C6 · Profile discovery

- [ ] `workspace.describe`'s `profiles` array lists every active profile as `<name>/<version>`.
- [ ] `core/1.0` is always present.

---

## Profile attestations

For each profile this implementation supports, copy the appropriate
section and tick every box. **An implementation MUST NOT advertise
a profile it does not pass.**

### Profile: `review/1.0`

- [ ] Implements `review.request`, `decide.approve`, `decide.reject`, `decide.override`, `abstain.declare`, `escalate.raise`.
- [ ] Adds `review_requested`, `abstained`, `escalated` task states.
- [ ] `decide.override`'s `diff` is validated as a well-formed RFC 6902 JSON Patch and applied deterministically.
- [ ] Override entries preserve `rationale`, `tags`, `policy_refs` as queryable audit data.
- [ ] `audit.read` filters support `method = decide.override`.
- [ ] Returns `-32010` … `-32013` for review-specific failures (see [`../profiles/review.md`](../profiles/review.md) §5).

### Profile: `whisper/1.0`

- [ ] Implements `whisper.ask` and `whisper.answer`.
- [ ] Enforces `deadline_ms`; emits a `whisper_lapsed` notification with the default applied.
- [ ] Validates `answer_option` is in the original option set.
- [ ] Returns `-32020` … `-32022` for whisper-specific failures.

### Profile: `deliberation/1.0`

- [ ] Implements `deliberate.open`, `deliberate.comment`, `deliberate.vote`, `deliberate.close`.
- [ ] Supports rules `any_one_approves`, `all_approve`, `quorum:N`, `weighted_vote:T`, `weighted_vote_with_veto:T`.
- [ ] Vetoes are preserved in the audit log.
- [ ] Returns `-32030` … `-32033` for deliberation-specific failures.

### Profile: `modes/1.0`

- [ ] `workspace.describe` exposes `mode` and `mode_ceiling`.
- [ ] `task.create` rejects tasks whose `mode` exceeds `mode_ceiling` with `-32040`.
- [ ] `shadow` tasks complete without delivering output.
- [ ] `trial` tasks force review-required regardless of per-task settings.
- [ ] Mode-ceiling changes require step-up auth when `identity-oidc` is in use.

### Profile: `handoff/1.0`

- [ ] Implements `handoff.propose`, `handoff.accept`, `handoff.decline`.
- [ ] `handoff.accept` atomically reassigns all listed tasks and emits a notification.
- [ ] Group handoffs route to all members; first accepter wins.
- [ ] Returns `-32050` … `-32052` for handoff-specific failures.

### Profile: `control/1.0`

- [ ] Implements `control.pause`, `control.resume`, `control.cancel`, `control.supersede`, `control.snapshot`, `control.rollback`.
- [ ] `control.rollback` appends; it never truncates the audit log.
- [ ] Privileged operations require step-up auth when `identity-oidc` is in use.
- [ ] Returns `-32060` … `-32063` for control-specific failures.

### Profile: `security-signed/1.0`

- [ ] Envelopes carry a top-level `sig` field of the form `ed25519:<kid>:<base64-sig>`.
- [ ] Signing canonicalises with RFC 8785 (JCS), with `sig` removed.
- [ ] Signatures use RFC 8032 Ed25519.
- [ ] Public keys are advertised at `participant.join` and validated on subsequent envelopes.
- [ ] `participant.rotate_key` requires the old key's signature.
- [ ] Revoked keys remain valid for verifying messages dated before revocation.
- [ ] Returns `-32070` … `-32073` for signature-specific failures.
- [ ] Passes the RFC 8032 test-vector validation in [`./test-vectors.md`](./test-vectors.md) §1.

### Profile: `audit-scitt/1.0`

- [ ] Each accepted envelope is wrapped as a COSE_Sign1 SCITT statement and submitted to a SCITT Transparency Service.
- [ ] SCITT receipts are returned to participants and verifiable offline against the service's published public key.
- [ ] The audit log uses SCITT signed statements and receipts (not a bespoke chain format).
- [ ] Returns `-32080` … `-32082` for SCITT-specific failures.

### Profile: `identity-oidc/1.0`

- [ ] Participant signing keys are bound via OIDC `cnf.jwk` (RFC 7800) or DPoP (RFC 9449).
- [ ] Privileged operations enforce a step-up auth window (default 5 minutes); returns `-32402` when exceeded.
- [ ] ID-token verification covers `iss`, `aud`, `exp`, signature, and `cnf.jwk` match.
- [ ] Returns `-32402` … `-32405` for identity-specific failures.

### Profile: `identity-vc/1.0`

- [ ] Participant identity is established via a W3C Verifiable Presentation with a Data Integrity Proof.
- [ ] Holder binding (proof of possession) is verified at presentation time.
- [ ] Issuer trust is configurable per workspace.
- [ ] Revocation is checked at presentation time and periodically thereafter.
- [ ] Returns `-32410` … `-32414` for VC-specific failures.

---

## Sample attestation envelope

A published attestation is an in-toto Statement:

```json
{
  "_type":         "https://in-toto.io/Statement/v1",
  "subject":       [
    { "name": "hap-implementation:example-coordinator:1.4.2",
      "digest": { "sha256": "…" } }
  ],
  "predicateType": "https://hap.dev/conformance/v1",
  "predicate": {
    "profiles_claimed": ["core/1.0", "review/1.0", "modes/1.0", "security-signed/1.0"],
    "tested_at":        "2026-05-17T18:00:00Z",
    "test_results":     { "core": "pass", "review": "pass", "modes": "pass", "security-signed": "pass" },
    "checklist_uri":    "https://example.org/attestations/hap-2026-05-17.md",
    "signer":           "did:example:example-org#attestation-key"
  }
}
```

This format integrates with standard supply-chain tooling (Sigstore,
Rekor, in-toto verifiers). Implementations are NOT required to host
their own infrastructure for attestations — publishing the JSON
above to any reachable URL is sufficient.

---

## Recommended starter sets

For deployments looking for a sensible profile combination, the
following starter sets cover most cases:

| Set name     | Profiles                                                                              |
|--------------|---------------------------------------------------------------------------------------|
| Minimal      | `core/1.0` + `security-signed/1.0`                                                    |
| Recommended  | `core/1.0` + `security-signed/1.0` + `review/1.0` + `modes/1.0` + `identity-oidc/1.0` |
| Regulated    | Recommended + `audit-scitt/1.0` + `deliberation/1.0` + `identity-vc/1.0`              |
| Full         | All ten profiles + Core                                                               |

These are conventional names, not normative. An implementation
attests to the specific profiles it implements; the set name is
shorthand.
