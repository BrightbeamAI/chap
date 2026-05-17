# HAP FAQ

Common questions about the Human-Agent Protocol, grouped by theme.
If you don't see your question here, the [Handbook](./HANDBOOK.md)
covers most operational topics in depth.

---

## Positioning

### Is HAP a competitor to MCP or A2A?

No. HAP is designed to compose with both:

- **MCP** lets an agent talk to tools. When a HAP-resident agent
  calls an MCP tool to do its work, the call is *cited* inside the
  agent's HAP artefact, with input/output hashes providing
  cryptographic boundaries.
- **A2A** lets agents talk to other agents across organisational
  boundaries. When a HAP participant needs to delegate work to an
  external A2A peer, the peer is represented as a `service:bridge…`
  participant inside the workspace. A2A traffic stays on the A2A
  wire; the bridge mediates.

The three protocols own different layers of the same stack. See
[`integrations/HAP-with-MCP.md`](./integrations/HAP-with-MCP.md) and
[`integrations/HAP-with-A2A.md`](./integrations/HAP-with-A2A.md).

### Why not just use Slack/Teams plus a bot framework?

You can. For internal-team chat with the occasional bot, Slack is
fine. HAP exists because:

1. Slack's audit semantics are vendor-specific. HAP's audit log is
   portable across implementations.
2. Slack has no protocol-level concept of structured override.
   Edits to bot output are lost as plain conversation.
3. Slack offers no mode promotion ladder. New bots are released or
   not; there's no shadow/trial scaffolding.
4. Slack is a closed product. HAP is an open standard.

If your problem is internal team coordination, Slack is the answer.
If your problem is producing verifiable, structured work product
where humans and agents collaborate, HAP is the answer.

### Why not just use a workflow engine like Temporal or Airflow?

A workflow engine handles **how** work happens — retries, durability,
fan-out, scheduling. HAP handles **what** the work is and **who**
did each step. They're complementary: a Temporal workflow can use
HAP envelopes to record its human-touchpoints; the audit log is
HAP's, the orchestration is Temporal's.

### What does "third pillar" actually mean?

The three protocols form a stack:

```
   ┌─────────────────────────────────────┐
   │  HAP — humans + agents collaborate  │
   ├─────────────────────────────────────┤
   │  A2A — agents talk to agents        │
   ├─────────────────────────────────────┤
   │  MCP — agents talk to tools         │
   └─────────────────────────────────────┘
```

You can adopt any one alone. They become more useful together: an
agent in a HAP workspace can use MCP for tools and A2A for remote
peers, all auditable through the HAP layer.

---

## Adoption

### How long does it take to implement HAP?

- **HAP Core**, in any language with basic JSON tooling: a weekend.
  ~300–500 LOC. The reference at [`reference/core/`](./reference/core/)
  is ~400 lines of TypeScript.
- **HAP Core + `review` profile**: another day. ~150 LOC of additional
  state-machine and method handlers.
- **A production deployment** with `identity-oidc`, `security-signed`,
  durable audit, monitoring: weeks. The protocol parts are small;
  the operational parts (HA, retention, incident playbooks) are the
  usual production engineering.

### Can I implement Core and call myself HAP-compliant?

Yes. Implementing all 7 Core methods plus the wire format and audit
log makes you Core-conformant. You file an in-toto attestation
saying so; you don't need anyone's permission.

### Which profiles are "must have"?

For most production deployments: `review`. That's where the
override-as-data dividend lives.

After that, in order of typical demand:

1. `modes` — safe rollout.
2. `identity-oidc` — verified human identity.
3. `control` — operational control plane.
4. `security-signed` — non-repudiation.
5. `audit-scitt` — regulated audit.
6. `deliberation`, `handoff`, `whisper`, `identity-vc` — workflow-specific.

### Do I need every profile to be useful?

No. A Core-only deployment with manual human review off-protocol is
useful. A Core + `review` deployment is significantly more useful.
Profiles are additive; you don't pay for what you don't use.

### Is HAP versioned?

The wire format and methods are stable. Each profile carries its
own semantic version (`review/1.0`, `core/1.0`, etc.) so an
implementation can advertise which profiles it implements and at
which version. Changes follow the HEP process in
[`GOVERNANCE.md`](./GOVERNANCE.md).

### Will my Core implementation keep working as profiles evolve?

Yes. Profiles are independent of Core; Core's wire format is
backward-compatible. A profile change that breaks an existing
profile implementation requires that profile's major-version bump,
documented in [`CHANGELOG.md`](./CHANGELOG.md).

---

## Identity and security

### Why doesn't Core require message signing?

Because most internal deployments don't need it, and requiring it
at Core would have made Core much harder to implement. Core works
fine over TLS + bearer tokens or mTLS. When non-repudiation matters,
add the `security-signed` profile.

### Why Ed25519 and not RSA or ECDSA?

Ed25519 is fast, has small signatures (64 bytes), has no parameter
ambiguity, and has been adopted by every major protocol that
specified a signature scheme in the last decade (TLS 1.3, SSH,
DNSSEC, COSE). RSA's parameter space and ECDSA's nonce-handling
pitfalls don't add to a protocol that's already well-served by
Ed25519.

If you need a different scheme for compliance reasons, the
`security-signed` profile's signature format is `<alg>:<kid>:<sig>`
and the spec leaves room for additional algorithms. Ed25519 is
required as the baseline; others may be added by HEP.

### Does HAP require a specific identity provider?

No. The `identity-oidc` profile works with any OIDC-compliant IdP
that supports `cnf.jwk` or that you can wrap with a DPoP-issuing
shim. The `identity-vc` profile works with any W3C-compliant VC
issuer. There's no vendor dependency at the protocol layer.

### How does HAP handle GDPR / right-to-be-forgotten?

Append-only logs and erasure rights are in genuine tension. HAP's
pattern (see [`HANDBOOK.md`](./HANDBOOK.md) §8.3 and
[`SECURITY.md`](./SECURITY.md) §6):

1. Pseudonymise personal data in envelopes wherever possible.
2. Reference any remaining personal data via redaction keys
   maintained in a separate, mutable side-store.
3. Exercise erasure rights by rotating/destroying the redaction
   key; the envelope's hash and signature remain intact, but the
   cleartext is unrecoverable.

This preserves audit-chain integrity while respecting subject rights.

### What's the threat model?

Documented in [`SECURITY.md`](./SECURITY.md). In short:

- Transport may be observed; integrity does not depend on
  confidentiality.
- Coordinator is trusted for routing and ordering, **not** for
  content. A malicious Coordinator cannot forge content.
- Individual participant compromise must not destroy the integrity
  of decisions by other participants.
- Replay and reorder must be detectable.

Out of scope: protocol-level confidentiality (use TLS),
denial-of-service resistance (use normal rate-limiting),
side-channel attacks on the signing implementation (use a hardened
library).

---

## Audit and compliance

### Is HAP suitable for regulated environments?

Yes, with the right profile combination. A regulated deployment
typically uses `core` + `review` + `identity-oidc` (or
`identity-vc`) + `security-signed` + `audit-scitt`. This gives you:

- Verified participant identity.
- Per-message non-repudiation.
- Cryptographic transparency log offline-verifiable by any auditor.
- Structured decision records (approvals, rejections, overrides,
  abstentions, escalations).
- Step-up auth for privileged operations.

Specific regulatory fits — SOX, HIPAA, GDPR, FDA QSR, EU AI Act —
depend on your deployment's controls; the protocol provides the
hooks.

### Does the audit log work offline?

With the `audit-scitt` profile, yes. SCITT receipts are
self-contained: anyone with the receipt and the transparency
service's public key can verify it without contacting the
Coordinator.

Without `audit-scitt`, the audit log is queryable via `audit.read`
on the Coordinator; offline verification of individual entries is
not provided.

### How long should I keep the audit log?

Deployment policy. Typical:

- Operational workspaces: 1–2 years.
- Compliance-relevant: 7 years.
- Healthcare-regulated: 10+ years.

The protocol doesn't impose a retention policy.

### Who runs the SCITT service?

You, a third-party notary, or a federation. SCITT is a standard;
the service is software. Recommended: a third party for production
to avoid the Coordinator being its own notary, but a same-org
service is fine for development.

---

## Composition and interop

### Can I mix HAP with my existing app?

Yes. HAP is a protocol, not a framework. A common pattern:

- Your app's UI calls HAP methods as if calling any other API.
- Your app's database mirrors the workspace state for UI rendering.
- Your app's audit needs are met by querying the HAP audit log.
- Your existing identity system feeds OIDC tokens to HAP.

You don't replace your app; you adopt HAP for the multi-party-
collaboration parts.

### What if I'm already using MCP?

You keep using it. HAP citations inside artefacts reference MCP
tool calls with input/output hashes; the MCP traffic stays on the
MCP wire. Nothing needs to change about your MCP setup.

### What about A2A?

Same answer. A2A peers appear inside HAP workspaces as
`service:bridge…` participants. The bridge mediates; A2A traffic
stays on the A2A wire.

### Can I use HAP without MCP or A2A?

Yes. They're complementary, not required. A HAP-only deployment
works perfectly well; tools and remote peers are then handled by
whatever mechanism you already use.

### Can HAP carry binary content?

Envelopes are JSON, so large binary content (images, PDFs, audio) is
referenced by URL or content-addressed hash, not embedded base64.
Small payloads (a kilobyte or two) can be embedded as base64 in
artefacts; anything larger should be stored separately and
referenced.

---

## Governance and licensing

### Who runs HAP?

HAP is developed in the open. Substantive changes go through the
HEP (HAP Enhancement Proposal) process: discussion, reference
implementation, public comment, ratification. See
[`GOVERNANCE.md`](./GOVERNANCE.md) for the full mechanics.

### Is HAP free to use?

Yes. The specification is licensed CC-BY 4.0; the reference code is
Apache 2.0. Implement freely, fork freely, redistribute freely.
Patent licence granted via Apache 2.0 for code contributions.

### Can my company contribute?

Yes. See [`CONTRIBUTING.md`](./CONTRIBUTING.md). Substantive
changes go through HEPs; small changes (clarifications, typos,
schema fixes) are PRs.

### Can my company brand a fork?

Yes, with three constraints (see [`GOVERNANCE.md`](./GOVERNANCE.md) §7):

1. Use a name clearly distinct from HAP.
2. Don't claim HAP conformance unless you pass the suite.
3. Submit changes back upstream if they should be part of HAP.

---

## Implementation specifics

### What language should I implement HAP in?

Any language with HTTP, JSON, and (for `security-signed`) Ed25519
and JCS libraries. Reference implementations are in TypeScript;
ports to Go, Python, Rust, Java, and C# are straightforward.

### Do I need a database for Core?

No. Core works fine with in-memory state for testing, and the
reference implementation uses Maps. For production, swap to any
durable store — Postgres, SQLite, S3 + manifest, whatever fits.
The protocol has no opinion.

### How do I handle high throughput?

Core's bottleneck is typically the audit log. Two patterns:

1. **Single-writer Coordinator** with append-only storage:
   Postgres COPY, or Kafka as the log.
2. **Sharded by workspace**: each Coordinator owns a subset of
   workspaces. Inter-workspace traffic uses the bridge pattern.

Most deployments don't approach the throughput limits before the
audit-volume limits hit first; size for storage.

### What about partial failures and idempotency?

HAP envelope IDs are unique; a Coordinator MUST de-duplicate by
`(workspace, id)`. Retrying a failed request with the same envelope
is safe. Operations that mutate state are idempotent on envelope ID.

### How do real-time UIs get updates?

A WebSocket binding to the same wire format. The Coordinator
push-delivers envelopes the participant is the recipient of; the
participant receives them as if they were the response to a
long-poll. Same envelopes, same JSON-RPC shape, different transport.

---

## What's next

- For the operational guide: [`HANDBOOK.md`](./HANDBOOK.md).
- For the wire-level details: [`core/SPEC.md`](./core/SPEC.md) and
  the profile docs.
- For end-to-end scenarios: [`examples/`](./examples/).
- For composition with adjacent protocols: [`integrations/`](./integrations/).
- For governance and contribution: [`GOVERNANCE.md`](./GOVERNANCE.md) and [`CONTRIBUTING.md`](./CONTRIBUTING.md).
