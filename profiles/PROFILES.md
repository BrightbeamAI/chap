# CHAP Profiles

A **profile** is an optional, named, versioned extension to CHAP
Core. Each profile adds a focused capability — message signing,
SCITT-backed audit, structured review, multi-party deliberation,
etc. — without changing Core.

Profiles are designed so an implementation can pick exactly the set
it needs, and a workspace's `workspace.describe` advertises which
profiles are active.

---

## 1. The profile catalogue

Each row is a self-contained document.

| Profile                                                       | What it adds                                                       | When you need it                                              |
|---------------------------------------------------------------|--------------------------------------------------------------------|---------------------------------------------------------------|
| [`security-signed`](./security-signed.md)                     | Ed25519 message signing, JCS canonicalisation, key rotation.       | Cross-trust-boundary deployments; non-repudiation requirement.|
| [`audit-scitt`](./audit-scitt.md)                             | Cryptographic transparency log via IETF SCITT.                     | Regulatory audit; offline-verifiable proofs.                  |
| [`identity-oidc`](./identity-oidc.md)                         | OIDC-bound human identity with `cnf.jwk` and step-up auth.         | Most enterprise SaaS deployments.                             |
| [`identity-vc`](./identity-vc.md)                             | W3C Verifiable Credentials for richer identity claims.             | Regulated professions; cross-org credentials.                 |
| [`review`](./review.md)                                       | `review.request`, `decide.approve`/`reject`/`override`, `abstain`. | Humans approving agent output. **The highest-value profile.** |
| [`whisper`](./whisper.md)                                     | Deadline-bound interrupt questions with default-if-lapsed.         | Mid-task disambiguation.                                      |
| [`deliberation`](./deliberation.md)                           | Multi-party voting with quorum, weights, vetoes.                   | Group decisions; release-gate boards.                         |
| [`modes`](./modes.md)                                         | Shadow / Trial / Production task mode ladder.                      | Safely rolling out new agents.                                |
| [`handoff`](./handoff.md)                                     | `handoff.propose`/`accept`/`decline` between participants.         | Shift changes; follow-the-sun coverage.                       |
| [`routing`](./routing.md)                                     | `task.route`, `review.depth`, `escalate.auto` driven by `routing_hints`. | Runtime-factor-driven routing: cost, criticality, confidence. |
| [`control`](./control.md)                                     | Pause, resume, snapshot, supersede, rollback.                      | Production operations; incident response.                     |

Two integration documents complement the profiles:

- [`../integrations/CHAP-with-MCP.md`](../integrations/CHAP-with-MCP.md) — how to cite MCP tool calls inside CHAP artefacts.
- [`../integrations/CHAP-with-A2A.md`](../integrations/CHAP-with-A2A.md) — how to bridge cross-organisation peers as `service:` participants.

---

## 2. How profile discovery works

A Coordinator advertises its active profiles in `workspace.describe`:

```json
{
  "profiles": [
    "core/1.0",
    "review/1.0",
    "modes/1.0",
    "security-signed/1.0"
  ]
}
```

Clients check this list before attempting profile-specific methods.
A client that uses an advertised profile is conformant; a client
that uses an unadvertised profile MUST be prepared for
`-32601 Method not found` errors.

---

## 3. Picking profiles for your use case

Common combinations:

### Internal-team chatbot

`core` only. The conversation lives in your existing chat platform;
CHAP just structures the task delegation and audit. No crypto, no
external dependencies.

### Production support-triage workspace

`core` + `review` + `modes`. Agents draft; humans review or override;
new agent versions roll out via shadow → trial → production.

### High-stakes triage with cost-aware routing

`core` + `review` + `modes` + `routing`. Same as above, plus the
`routing/1.0` profile decides which agent (or human pool) handles
each task based on criticality, deadline, and budget hints carried
on the task. Review depth is decided per-artefact from measured
confidence and cost.

### Regulated approval workflow

`core` + `review` + `deliberation` + `identity-oidc` + `security-signed`
+ `audit-scitt`. Signed, transparency-logged, multi-party-approved
audit suitable for regulatory examination.

### Cross-organisation collaboration

`core` + `review` + `identity-vc` + the MCP/A2A integration patterns.
Verified credentials cross the trust boundary; A2A bridge participants
mediate the cross-org work.

### Solo developer with agents

`core` + `review`. The override-capture data is your tuning signal.

---

## 4. Profile dependencies

Most profiles are independent. Some build on others:

| Profile         | Depends on                            |
|-----------------|---------------------------------------|
| `review`        | (Core)                                |
| `whisper`       | (Core)                                |
| `deliberation`  | (Core)                                |
| `modes`         | (Core)                                |
| `handoff`       | (Core)                                |
| `routing`       | (Core); composes with `review/1.0` (depth feeds review trigger) and `modes/1.0` (modes set upper bound on enforcement). |
| `control`       | (Core); strongly recommended with `modes` for full effect. |
| `security-signed` | (Core)                              |
| `audit-scitt`   | `security-signed`                     |
| `identity-oidc` | (Core); composes with `security-signed` for key binding. |
| `identity-vc`   | (Core); composes with `security-signed` for key binding. |

Profiles that don't depend on each other can be added or removed
without affecting the others.

---

## 5. Versioning profiles

Each profile versions independently. A workspace can announce
`review/1.0` and `modes/0.2`. Profile versions follow the same
semver rules as Core (see [`../GOVERNANCE.md`](../GOVERNANCE.md) §4).

Within v0.x, profiles may include small breaking changes between
minor versions if announced in `CHANGELOG.md`. Stability is promised
at the 1.0 release.

---

## 6. Writing a new profile

If you have a workflow pattern not covered by an existing profile,
the path to standardising it is:

1. Write a HEP (CHAP Enhancement Proposal) — see [`../GOVERNANCE.md`](../GOVERNANCE.md) §3.2.
2. Build a reference implementation alongside the proposal.
3. Demonstrate at least one production use.
4. Submit the profile spec as a PR to `profiles/<name>.md`.

Profiles MUST NOT:

- Redefine Core methods in incompatible ways.
- Require modifications to the JSON-RPC envelope shape.
- Force every workspace to enable them.

Profiles MAY:

- Add new methods.
- Add new envelope fields under `params`.
- Add new error codes in the `-32000` to `-32099` CHAP-private range.
- Tighten Core's optional behaviours (e.g. require signatures).
- Add new state-machine transitions on existing entities.

---

## 7. Why profiles, not "extension points"

Extension points without a registry tend to fragment. By contrast,
profiles are:

- **Named.** Two implementations claiming the same profile mean
  the same thing.
- **Versioned.** A change is visible.
- **Discoverable.** Clients know what's available before they
  try.
- **Composable.** Two orthogonal profiles can be enabled together
  without ambiguity.

If you find yourself reaching for "let's just add a custom field
here," the right move is a profile, not an ad-hoc extension.
