# Security Policy

This document describes CHAP's security model, its assumptions, the threats it
defends against, the threats it explicitly does **not** defend against, and
how to report vulnerabilities responsibly.

---

## 1. Threat model

CHAP is designed for environments where humans, agents, and services share a
collaboration workspace and produce decisions whose provenance must be
verifiable after the fact. The model assumes:

- **The transport may be observed.** TLS 1.3+ is required for production
  deployments, but the protocol's integrity guarantees do not depend on
  transport confidentiality.
- **The Coordinator is trusted for routing and ordering, but not for content.**
  Every message is signed at its origin. A malicious Coordinator can drop,
  delay, or reorder messages, but it cannot forge content attributed to a
  participant whose key it does not hold.
- **Participants may be compromised individually.** A single compromised key
  must not destroy the integrity of decisions made by other participants.
- **Replay must be detectable.** Hash-chained evidence and monotonic timestamps
  ensure that any reorder, replay, or insertion is visible to a verifier.

### Threats in scope

| #  | Threat                                              | Mitigation                                                  |
|----|-----------------------------------------------------|-------------------------------------------------------------|
| T1 | Forgery of messages attributed to a participant     | Ed25519 signature over the JCS canonicalisation of every message |
| T2 | Replay of a captured message                        | Monotonic `ts`, unique `id`, and `prev_hash` linkage          |
| T3 | Reorder or selective drop by a malicious Coordinator| Hash-chained evidence; verifier detects gaps                 |
| T4 | Long-term key compromise                            | Short-lived OIDC-bound ephemeral keys; explicit rotation API  |
| T5 | Confused-deputy on tool calls                       | Per-method `required_scope`; explicit `privileged` flag      |
| T6 | Mode confusion (shadow output reaching production)  | Mode is an envelope field; Coordinator MUST enforce          |
| T7 | Identity spoofing across organisational boundaries  | OIDC binding for humans; SPIFFE/X.509 for agents and services|
| T8 | Audit log tampering                                 | Append-only chain; optional external anchoring               |
| T9 | Privilege escalation via crafted artefact           | Artefacts are content; methods carry the authority           |
| T10| Denial-of-service via oversized messages            | Hard limits on envelope size, recommended rate limits        |

### Threats out of scope

CHAP does **not** by itself defend against:

- **Content confidentiality.** CHAP does not encrypt artefact payloads in the
  evidence chain. Sensitive content must be referenced by URI and stored in
  a system with its own access controls, or wrapped with a future
  confidentiality extension (see CHANGELOG).
- **Side-channel attacks on the participant's environment.** Key extraction
  via local malware, screen-scraping a human reviewer, or prompt-injecting
  an agent are out of scope.
- **Social engineering of human participants.** CHAP records what was
  decided, not whether the decider was tricked.
- **Compromise of the identity provider.** If the OIDC IdP issues a token
  to the wrong principal, CHAP will faithfully record decisions made under
  that token.
- **Quantum adversaries.** Ed25519 is not post-quantum. A future revision
  will offer hybrid signatures.

---

## 2. Cryptographic primitives

| Purpose                          | Algorithm                                |
|----------------------------------|------------------------------------------|
| Message signing                  | Ed25519 (RFC 8032)                       |
| Canonicalisation for signing     | JCS (RFC 8785)                           |
| Hash chain                       | SHA-256                                  |
| Artefact integrity (optional)    | SHA-256 over canonical bytes             |
| Transport                        | TLS 1.3 (production); mTLS recommended for service-to-service |
| Identity, human                  | OIDC ID token with `cnf.jwk` confirmation claim (DPoP-style binding) |
| Identity, agent or service       | SPIFFE ID, X.509, or OIDC client credentials |

The wire format does **not** prescribe a key-exchange or a session-key scheme;
keys are long- or short-lived signing keys, and TLS handles transport
confidentiality.

---

## 3. Key management

### Human keys

Human signing keys SHOULD be **ephemeral** (typical lifetime: hours) and bound
to an OIDC session via the `cnf.jwk` confirmation claim, in the style of DPoP
(RFC 9449). The Coordinator MUST verify both the signature and the token
binding before accepting a message.

Rotation is initiated by `participant.rotate_key`. The old key remains valid
for verification of historical evidence but MUST NOT be accepted on new
messages after the grace window (default: 5 minutes).

### Agent and service keys

Agents and services SHOULD use **workload identities** (SPIFFE SVIDs, mTLS
client certificates, or OIDC client credentials with a bound JWK). Lifetime
is typically hours to days, with automatic rotation by the workload identity
infrastructure.

### Coordinator keys

The Coordinator signs evidence-chain checkpoints (every N entries, default
1000) with a long-lived organisational key. This is the trust anchor for
long-term audit. The key SHOULD be backed by an HSM in production.

---

## 4. Privileged operations

Methods marked `privileged: true` in [`schemas/chap-methods.schema.json`](./schemas/profiles/chap-methods.schema.json)
require step-up authentication. The Coordinator MUST enforce that the
caller's most recent OIDC `auth_time` is within the configured step-up
window (default: 5 minutes) and that the caller's token carries an
appropriate `acr` (authentication context class reference) value.

Privileged operations include: `control.pause`, `control.rollback`,
`control.snapshot`, `workspace.set_mode` (to `production`), and
`workspace.invite` (for admin roles).

---

## 5. Evidence chain integrity

Every message carries `evidence.prev_hash`, computed as
`SHA-256(JCS(previous_message_minus_evidence) || previous_evidence.sig)`.
A verifier replays the chain from the workspace's genesis entry and checks:

1. Every signature verifies against the claimed `from` participant's
   declared public key at the message's timestamp.
2. Every `prev_hash` matches the recomputed hash of the previous entry.
3. Timestamps are monotonically non-decreasing.
4. No `id` appears twice.
5. Coordinator checkpoints (when present) verify against the Coordinator's
   long-lived key.

For deployments that need stronger guarantees against Coordinator
compromise, the chain head MAY be anchored externally — e.g. periodic
publication to a transparency log or an internal append-only store with
separate access controls.

---

## 6. Mode safety

The `mode` field on tasks and messages is the protocol's mechanism for
preventing shadow or trial output from leaking into production effects.
A conformant Coordinator MUST:

- Reject any `task.assign` whose mode exceeds the workspace's declared
  ceiling.
- Refuse to dispatch artefacts produced in `shadow` mode to participants
  whose role is not on the workspace's `shadow_observers` list.
- Require step-up auth and an explicit policy match before accepting
  `workspace.set_mode` transitions toward `production`.
- Record every mode transition as a first-class evidence entry.

---

## 7. Reporting a vulnerability

If you believe you have found a security issue in this specification, in the
reference implementation, or in a deployed CHAP system you operate, please
follow **coordinated disclosure**:

1. **Do not** open a public issue.
2. Send a report to the security contact for the deployment in question,
   or — for issues in this specification itself — to the address listed in
   the repository's `SECURITY-CONTACT` file or the project's website.
3. Include enough detail to reproduce: affected version, message sequence,
   environment, and expected vs. actual behaviour.
4. Allow at least 90 days for a fix before disclosure, or coordinate a
   shorter timeline if the issue is already being exploited.

We commit to acknowledging reports within 5 business days, providing a
preliminary assessment within 15 business days, and publishing a fix and
advisory within 90 days where feasible.

---

## 8. Known limitations

These are not vulnerabilities but explicit gaps tracked for the next draft:

- **No confidentiality for evidence-chain content.** Use opaque artefact
  URIs and external content storage for sensitive data, or wait for the
  confidentiality extension.
- **No formal interop test suite.** Conformance is currently asserted by
  test vectors and self-attested checklists (see [`conformance/`](./conformance/)).
- **No post-quantum signatures.** Hybrid Ed25519 + ML-DSA is under discussion.
- **No standard revocation gossip.** Compromised keys are revoked at the
  Coordinator; cross-workspace revocation is deployment-specific.
