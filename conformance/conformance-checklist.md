# CHAP Conformance Checklist

This is the self-attestation template for CHAP. An
implementation claims conformance by:

1. Filling in the **Core** checklist below.
2. Filling in a **profile checklist** for each profile it implements.
3. Publishing the completed attestation as an
   [in-toto attestation](https://github.com/in-toto/attestation) with
   subject `chap-implementation:<name>:<version>` and predicate
   `chap.dev/conformance/v1`.

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
CHAP conformance.

### C1 · Wire format

- [ ] Envelopes are valid [JSON-RPC 2.0](https://www.jsonrpc.org/specification) requests, responses, or notifications.
- [ ] Required CHAP fields (`workspace`, `from`, `to`, `ts`) are present inside `params`.
- [ ] `ts` is RFC 3339 with millisecond precision.
- [ ] Participant URIs match the grammar in [`../SPECIFICATION.md`](../SPECIFICATION.md#51-participant-uri-scheme) §5.1.

### C2 · Transport

- [ ] Accepts CHAP envelopes over HTTP POST to a documented path (`/chap` recommended).
- [ ] Uses TLS in production deployments.
- [ ] Returns the corresponding response envelope in the HTTP response body.

### C3 · The 7 methods

- [ ] `workspace.describe` returns `id`, `created`, `state`, `members`, `profiles`, `audit_count`.
- [ ] `participant.join` adds the participant to `members`; rejects with `-32602` for missing fields.
- [ ] `participant.leave` removes the caller, who must be a current member; a second leave is refused with `-32011` and changes nothing.
- [ ] `task.create` validates that the assignee is a current member; returns `task_id` and `state: "created"`.
- [ ] Every actor-action method validates that `from` (the actor) is a current member, rejecting a non-member with `-32011` (SPECIFICATION.md §6.3.1). Harness vector `rv-07` verifies this for `decide.approve`; `rv-08` tests the reviewer set.
- [ ] `task.update` enforces the transition table in [`../SPECIFICATION.md`](../SPECIFICATION.md#81-lifecycle) §8.1; rejects a transition the table does not list with `-32602`.
- [ ] `task.complete` is refused on a task in `cancelled`, `superseded`, `paused` or any state the §8.1 table does not list for it, with `-32602`.
- [ ] `audit.read` supports `range` and at minimum the `method`, `from`, `task_id` and `outcome` filters; returns `entries` and `next_seq`.

### C4 · Audit log

- [ ] Every accepted state-changing envelope is appended in arrival order. `workspace.describe`, `audit.read`, `audit.verify_chain`, `audit.verify_receipt` and `audit.submit_to_scitt` are never appended, and a `task.create` answered from a seen `idempotency_key` is not appended again.
- [ ] Every refused call that [`../SPECIFICATION.md`](../SPECIFICATION.md) §10.1 names is appended as a refusal entry, with the call under `request` and an `outcome` giving the code, and no other refusal is appended. Verified by `refusal-record-vectors.json`, and over HTTP by harness vector `rv-13`.
- [ ] A call is checked in the order [`../SPECIFICATION.md`](../SPECIFICATION.md) §10.1 gives. A signed copy of a recorded refusal, compared without its `sig`, is answered with that refusal and `data.refused_at_seq` and is not evaluated; a signed copy of an accepted call that is refused is not recorded. `refusal-record-vectors.json` checks the signed-copy rules and that the request's own checks come before the pause.
- [ ] Each entry records the Coordinator's arrival timestamp.
- [ ] `audit.read` results are stable: a range within the log returns the same entries every time.

### C5 · Error handling

- [ ] Returns `-32700` for malformed JSON.
- [ ] Returns `-32600` for non-JSON-RPC-2.0 requests.
- [ ] Returns `-32601` for unknown methods.
- [ ] Returns `-32602` for missing or wrongly-typed parameters.
- [ ] Returns `-32603` for internal failures, with no leaked stack traces.

### C6 · Profile discovery

- [ ] `workspace.describe`'s `profiles` array lists every active profile as `<name>/<version>`.
- [ ] `core/1.0` is in the default profile set, and `workspace.set_profiles` adds it when missing. `workspace.create` advertises an explicit `profiles` list as given, adding `security-signed/1.0` or `identity-oidc/1.0` when the Coordinator enforces it.

---

## Profile attestations

For each profile this implementation supports, copy the appropriate
section and tick every box. **An implementation MUST NOT advertise
a profile it does not pass.**

### Profile: `review/1.0`

- [ ] Implements `review.request`, `decide.approve`, `decide.reject`, `decide.override`, `abstain.declare`, `escalate.raise`.
- [ ] Adds `review_requested`, `abstained`, `escalated` task states.
- [ ] `decide.override`'s `diff` is validated as a well-formed RFC 6902 JSON Patch and applied deterministically.
- [ ] Review decisions (`decide.*`, `abstain.declare`) require `from` to be one of the reviewers addressed in `review.request`'s `to` set; a member outside that set is rejected with `-32011` (see [`../profiles/review.md`](../profiles/review.md) §3.2). Verified by harness vector `rv-08`.
- [ ] `task.complete` on a task whose review is required opens a review and moves the task to `review_requested`, holding the submitted output as the artefact under review, and only a reviewer decision completes it (see [`../profiles/review.md`](../profiles/review.md) §3.1).
- [ ] The implicit review addresses the **human** members who are neither the completer nor the assignee, so neither a producer nor another agent can approve agent output; with no human eligible the completion is refused with `-32011`. An explicit `review.request` keeps whatever `to` it was given. Verified by harness vector `rv-12`.
- [ ] `review.request` is refused on a task that has been stopped (`cancelled`, `superseded`, `paused`) with `-32010`, so a review cannot revive terminated work or step around a pause (see [`../SPECIFICATION.md`](../SPECIFICATION.md#81-lifecycle) §8.1).
- [ ] The successor `escalate.raise` creates requires review when the original did, so escalating never removes a required review (see [`../profiles/review.md`](../profiles/review.md) §3.5). The successor `control.supersede` creates does too, whatever its own `review_required` says (see [`../profiles/control.md`](../profiles/control.md) §4).
- [ ] Override entries preserve `rationale`, `tags`, `policy_refs` as queryable audit data.
- [ ] `audit.read` filters support `method = decide.override`.
- [ ] Returns `-32010` … `-32014` for review-specific failures (see [`../profiles/review.md`](../profiles/review.md) §5).

### Profile: `whisper/1.0`

- [ ] Implements `whisper.ask` and `whisper.answer`.
- [ ] Applies `deadline_ms` through a lapse check that the host runs. The check marks each pending whisper past its deadline as lapsed, applies `default_if_lapsed`, records a `notify.message` of kind `whisper_lapsed` on the audit log and returns it to the host. Until the check has run, a `whisper.answer` that arrives after the deadline is accepted; once it has run, the answer is refused with `-32021`.
- [ ] Validates `answer_option` is in the original option set.
- [ ] Returns `-32020` … `-32022` for whisper-specific failures.

### Profile: `deliberation/1.0`

- [ ] Implements `deliberate.open`, `deliberate.comment`, `deliberate.vote`, `deliberate.close`.
- [ ] Supports rules `any_one_approves`, `all_approve`, `quorum:N`, `weighted_vote:T`, `weighted_vote_with_veto:T`.
- [ ] Vetoes are preserved in the audit log.
- [ ] Returns `-32030` … `-32033` for deliberation-specific failures.

### Profile: `modes/1.0`

- [ ] `workspace.describe` exposes `mode` and `mode_ceiling`.
- [ ] `task.create` rejects tasks whose `mode` exceeds `mode_ceiling` with `-32040`, and so do `control.supersede` and `escalate.raise` for the successor they create.
- [ ] A `shadow` task completes and stores its output like any other task.
- [ ] `trial` tasks force review-required regardless of per-task settings, so `task.complete` on one opens a review, and only a reviewer decision completes it. This holds for a task made by `task.create`, `control.supersede` or `escalate.raise`.
- [ ] Trial forces review only when this profile is loaded: on a workspace that has not declared `modes/1.0`, a `trial` task does not force review. The `mode_ceiling` check on `task.create` applies on every workspace, with `-32040`.
- [ ] Privileged operations, `control.set_mode_ceiling` included, require step-up auth when the Coordinator enforces step-up, an option separate from `identity-oidc/1.0`. A human or OIDC-bound caller whose `auth_time` is missing or outside the window is refused with `-32402`.

### Profile: `handoff/1.0`

- [ ] Implements `handoff.propose`, `handoff.accept`, `handoff.decline`.
- [ ] `handoff.accept` atomically reassigns all listed tasks and emits a notification.
- [ ] Group handoffs route to all members; first accepter wins.
- [ ] Returns `-32050` … `-32052` for handoff-specific failures.

### Profile: `routing/1.0`

- [ ] Implements `task.route`, `review.depth`, `escalate.auto`.
- [ ] Each method produces a `route_decision` artefact, kept in the Coordinator's store. The request that produced it is what reaches the audit chain.
- [ ] `route_decision` artefacts record `decision_type`, `outcome`, `policy_id`, `hints_observed`, and `rationale`.
- [ ] Without an operator policy, `task.route` selects the first member in `candidates` who is not paused. An operator policy may select any participant, in `candidates` or outside it; a selection that is not a member is refused with `-32510`, and one who is paused with `-32063`.
- [ ] `task.route`, `review.depth` and `escalate.auto` refuse a caller who is not a workspace member with `-32011`.
- [ ] `review.depth=spot_check` is accompanied by a `sampling_probability` in [0, 1].
- [ ] `escalate.auto=true` is accompanied by a `to` URI and a `triggered_rule` object.
- [ ] `task.route` reassigns the task in every mode, `shadow` and `trial` included.
- [ ] Returns `-32510` … `-32516` for routing-specific failures.
- [ ] Core-only nodes forward `routing_hints` on tasks and artefacts unchanged.

### Profile: `control/1.0`

- [ ] Implements `control.pause`, `control.resume`, `control.cancel`, `control.supersede`, `control.snapshot`, `control.rollback`.
- [ ] `control.rollback` appends; it never truncates the audit log.
- [ ] Privileged operations, `control.set_mode_ceiling` included, require step-up auth when the Coordinator enforces step-up, an option separate from `identity-oidc/1.0`. A human or OIDC-bound caller whose `auth_time` is missing or outside the window is refused with `-32402`.
- [ ] A paused participant is assigned no new tasks: `task.create`, `control.supersede` and `escalate.raise` refuse a paused assignee, and `handoff.accept` a paused acceptor, with `-32063`. The default `task.route` policy passes a paused candidate over, and an operator policy's choice of one is refused with `-32063`.
- [ ] Returns `-32061` … `-32063` for control-specific failures, and `-32402` when a step-up check fails.

### Profile: `security-signed/1.0`

- [ ] Envelopes carry a top-level `sig` field of the form `ed25519:<kid>:<base64-sig>`.
- [ ] Signing canonicalises with RFC 8785 (JCS), with `sig` removed.
- [ ] Signatures use RFC 8032 Ed25519.
- [ ] Public keys are advertised at `participant.join` and validated on subsequent envelopes.
- [ ] `participant.rotate_key` requires the old key's signature.
- [ ] Revoked keys remain valid for verifying messages dated before revocation.
- [ ] Returns `-32070` … `-32074` for signature-specific failures.
- [ ] Passes the RFC 8032 test-vector validation in [`./test-vectors.md`](./test-vectors.md) §1.

### Profile: `audit-scitt/1.0`

- [ ] `audit.submit_to_scitt` builds one statement for each entry in the requested range, an accepted envelope or a recorded refusal alike, and statements are built only when it is called. A statement is a JSON object shaped like COSE_Sign1: its payload is the canonical form of the record the entry's chain link hashes, and its `signature` is the placeholder `"<deployment-supplied>"`. With a submitter configured, each statement goes to it and the receipts are returned; without one, the statements are returned for submission out of band.
- [ ] SCITT receipts are returned to the caller of `audit.submit_to_scitt`, and `audit.verify_receipt` checks one through the deployment's verifier.
- [ ] The audit log itself is the Coordinator's own hash chain ([`./test-vectors.md`](./test-vectors.md) §3); SCITT statements are built from it on request.
- [ ] Returns `-32080` … `-32082` for SCITT-specific failures.
- [ ] `audit.verify_chain` returns `ok: true` only with `status: "verified"`, and `status: "not_evaluated"` whenever any entry lies outside the chain (SPECIFICATION.md §10.2, vectors `av-01` … `av-05`).
- [ ] `entries_checked` + `entries_unchecked` = `entries_total` in every verdict, and `checked_from_seq` is the first covered `seq` or `null`.

### Profile: `identity-oidc/1.0`

- [ ] Participant signing keys are bound via OIDC `cnf.jwk` (RFC 7800).
- [ ] When step-up is enforced, privileged operations apply a window (default 5 minutes) and return `-32402` when it is exceeded.
- [ ] ID-token verification covers `iss`, `aud`, `exp` and the signature; a token binds only to its participant (`-32404`).
- [ ] Returns `-32402` … `-32405` for identity-specific failures.

### Profile: `identity-vc/1.0`

- [ ] Participant identity is established via a W3C Verifiable Presentation with a Data Integrity Proof.
- [ ] Holder binding (proof of possession) is verified at presentation time.
- [ ] A join under an existing member's name accepts a presentation only from its recorded holder (`-32411`).
- [ ] Issuer trust is configurable per workspace.
- [ ] Revocation is checked at presentation time and periodically thereafter.
- [ ] Returns `-32410` … `-32413` for VC-specific failures.

---

## Sample attestation envelope

A published attestation is an in-toto Statement:

```json
{
  "_type":         "https://in-toto.io/Statement/v1",
  "subject":       [
    { "name": "chap-implementation:example-coordinator:1.4.2",
      "digest": { "sha256": "…" } }
  ],
  "predicateType": "https://chap.dev/conformance/v1",
  "predicate": {
    "profiles_claimed": ["core/1.0", "review/1.0", "modes/1.0", "security-signed/1.0"],
    "tested_at":        "2026-05-17T18:00:00Z",
    "test_results":     { "core": "pass", "review": "pass", "modes": "pass", "security-signed": "pass" },
    "checklist_uri":    "https://example.org/attestations/chap-2026-05-17.md",
    "signer":           "did:example:example-org#attestation-key"
  }
}
```

This format integrates with standard supply-chain tooling (Sigstore,
Rekor, in-toto verifiers). Implementations are NOT required to host
their own infrastructure for attestations, publishing the JSON
above to any reachable URL is sufficient.

---

## Recommended starter sets

For deployments looking for a sensible profile combination, the
following starter sets cover most cases:

| Set name        | Profiles                                                                              |
|-----------------|---------------------------------------------------------------------------------------|
| Minimal-signed  | `core/1.0` + `security-signed/1.0`                                                    |
| Recommended     | `core/1.0` + `security-signed/1.0` + `review/1.0` + `modes/1.0` + `identity-oidc/1.0` |
| Regulated       | Recommended + `audit-scitt/1.0` + `deliberation/1.0` + `identity-vc/1.0`              |
| Comprehensive   | All eleven profiles + Core                                                             |

These are conventional names, not normative levels. An implementation
attests to the specific profiles it implements; the set name is
shorthand. Note that the §17 conformance ladder
(*Minimal* / *Recommended* / *Full-planned*) is distinct from this
table: §17 names what level the implementation reaches; the table
above is a packaging convention for profile selection.
