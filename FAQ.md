# CHAP FAQ

Common questions about the Collaborative Human-Agent Protocol, grouped by theme.
If you don't see your question here, the [Handbook](./HANDBOOK.md)
covers most operational topics in depth.

---

## Positioning

### Is CHAP a competitor to MCP or A2A?

No. CHAP is designed to compose with both, in two complementary
directions:

**Outward (CHAP cites external events):**
- **MCP** lets an agent talk to tools. When a CHAP-resident agent
  calls an MCP tool to do its work, the call is *cited* inside the
  agent's CHAP artefact, with input/output hashes providing
  cryptographic boundaries.
- **A2A** lets agents talk to other agents across organisational
  boundaries. When a CHAP participant needs to delegate work to an
  external A2A peer, the peer is represented as a `service:bridge…`
  participant inside the workspace. A2A traffic stays on the A2A
  wire; the bridge mediates.

**Inward (CHAP exposes itself as an MCP server or A2A agent):**
- A CHAP Coordinator can present itself as an **MCP server** with
  every CHAP method as a tool. Claude Desktop, Cursor, Claude Code,
  or any MCP client can then drive a CHAP workspace from natural
  language. Reference servers ship in
  [`reference/mcp-server-ts/`](./reference/mcp-server-ts/) and
  [`reference/mcp-server-py/`](./reference/mcp-server-py/).
- The same Coordinator can present itself as an **A2A agent** with
  every CHAP method as an `AgentSkill`. Any A2A-aware orchestrator
  can register it by URL and delegate work. Reference servers ship
  in [`reference/a2a-server-ts/`](./reference/a2a-server-ts/) and
  [`reference/a2a-server-py/`](./reference/a2a-server-py/). The
  TypeScript adapter speaks A2A 0.3 and the Python adapter A2A 1.0.
  Milestone 0.5 moves the TypeScript adapter to A2A 1.0.

The three protocols own different layers of the same stack. See
[`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md) and
[`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

### Why not just use Slack/Teams plus a bot framework?

You can. For internal-team chat with the occasional bot, Slack is
fine. CHAP exists because:

1. Slack's audit semantics are vendor-specific. CHAP's audit log has
   one documented entry shape, the same in both reference
   coordinators; a portable export format arrives in milestone 0.6.
2. Slack has no protocol-level concept of structured override.
   Edits to bot output are lost as plain conversation.
3. Slack offers no mode promotion ladder. New bots are released or
   not; there's no shadow/trial scaffolding.
4. Slack is a closed product. CHAP is an open standard.

If your problem is internal team coordination, Slack is the answer.
If your problem is producing verifiable, structured work product
where humans and agents collaborate, CHAP is the answer.

### Why not just use a workflow engine like Temporal or Airflow?

A workflow engine handles **how** work happens, retries, durability,
fan-out, scheduling. CHAP handles **what** the work is and **who**
did each step. They're complementary: a Temporal workflow can use
CHAP envelopes to record its human-touchpoints; the audit log is
CHAP's, the orchestration is Temporal's.

### What does "third pillar" actually mean?

The three protocols form a stack:

```
   ┌─────────────────────────────────────┐
   │  CHAP   humans + agents collaborate  │
   ├─────────────────────────────────────┤
   │  A2A   agents talk to agents        │
   ├─────────────────────────────────────┤
   │  MCP   agents talk to tools         │
   └─────────────────────────────────────┘
```

You can adopt any one alone. They become more useful together: an
agent in a CHAP workspace can use MCP for tools and A2A for remote
peers, all auditable through the CHAP layer.

### What is actually novel about CHAP? Aren't attestation and audit logs already solved?

Standardised attestation already exists. SCITT, in-toto, and C2PA all provide
signed-statement substrates with verifiable receipts. CHAP does not reinvent
that layer; it profiles SCITT as the recommended audit anchor and reuses the
attestation mechanics underneath.

The contribution is the *vocabulary of collaboration events being attested*.
Before CHAP, every team that put humans and agents on the same work invented
its own shape for "the human overrode the agent's draft", "the human asked the
agent to clarify what it meant", "the senior reviewer escalated this above a
threshold", "the night-shift lead handed this queue to the morning shift with
context". Those shapes lived in application code and ad-hoc tool stacks, so
they were not portable, not auditable consistently, and not learnable from
across deployments. CHAP defines the wire-level vocabulary: override-with-diff-and-rationale,
abstain-with-reason, handoff-with-context, whisper-with-typed-options, mode
promotion under documented evidence, deliberation under quorum rules. The
audit story is the easiest to demonstrate, which is why it leads in most
introductions, but the deeper contribution is the collaboration vocabulary that
the audit happens to record.

---

## Adoption

### How long does it take to implement CHAP?

- **CHAP Core**, in any language with basic JSON tooling: a weekend.
  The reference at [`reference/core/`](./reference/core/) is a single
  short server file in TypeScript.
- **CHAP Core + `review` profile**: another day of state-machine and
  method handlers.
- **A production deployment** with `identity-oidc`, `security-signed`,
  durable audit, monitoring: weeks. The protocol parts are small;
  the operational parts (HA, retention, incident playbooks) are the
  usual production engineering.

### Can I implement Core and call myself CHAP-compliant?

Not under the 0.2 text: the **Minimal** level in
[SPECIFICATION.md §17](./SPECIFICATION.md#17-conformance) also needs
Ed25519 signing, the hash-chained log, `participant.describe` and
three `review/1.0` methods. Attest exactly what you implement and
which harness tests pass; milestone 0.4 replaces the levels.

### Which profiles are "must have"?

For most production deployments: `review`. That's where the
override-as-data dividend lives.

After that, in order of typical demand:

1. `modes`: safe rollout.
2. `identity-oidc`: verified human identity.
3. `routing`: cost/criticality-aware routing and review-depth decisions.
4. `control`: operational control plane.
5. `security-signed`: non-repudiation.
6. `audit-scitt`: regulated audit.
7. `deliberation`, `handoff`, `whisper`, `identity-vc`: workflow-specific.

### How do I handle cost, criticality, or confidence in CHAP?

Three things, and they're deliberately separated.

**The signals** live in Core. Every task and every artefact carries
an optional `routing_hints` object. Tasks declare what the work is
and what it's allowed to cost (`criticality`, `deadline`,
`max_cost_usd`, `risk_tier`). Artefacts declare what was produced
and at what cost (`confidence`, `model_id`, `cost_consumed_usd`,
`latency_ms`). CHAP records these with the call, signed under
`security-signed/1.0` and hashed when the chain is on, but assigns
them no semantics: confidence is model-specific, criticality is
operator-defined.

**The decisions** live in the optional `routing/1.0` profile.
Three methods: `task.route` picks an assignee from candidates,
`review.depth` decides whether to skip, spot-check, or fully review,
and `escalate.auto` evaluates rules and auto-escalates when they
fire. Each decision is a `route_decision` artefact citing the exact
hints it consulted. The Coordinator holds it in the workspace and
returns it to the caller, and the audit log records the request, so
keep the response if you need the reasons later.

**The policies themselves** live outside CHAP. The protocol carries
a `policy_id` that resolves under the workspace's
`routing_policy_uri`; what's in that document is the operator's
business. This keeps CHAP from becoming an opinionated policy
engine while still making routing decisions auditable end-to-end.

A workspace that wants cost-aware routing enables `core` + `review`
+ `modes` + `routing`. A workspace that doesn't need it still works
fine without, the hints are optional everywhere.

### Do I need every profile to be useful?

No. A Core-only deployment with manual human review off-protocol is
useful. A Core + `review` deployment is significantly more useful.
Profiles are additive; you don't pay for what you don't use.

### Does CHAP guarantee that human feedback actually reaches the next model?

No. CHAP makes the signal *portable and structured*: every override carries a
diff, a rationale, and tags. What anyone does with that signal (prompt revision,
fine-tuning corpus, retrieval re-grounding, none of the above) is a separate
engineering concern.

A team that ignores the data still has the data. A team that uses it has it in
a shape that is actually useful, indexed by tag and reviewer, with the original
artefact citeable and the chain replayable. CHAP does not close the loop; it
makes the loop possible to close, which is meaningfully less than closing it
but also meaningfully more than the status quo where the loop is impossible to
close because the signal was never captured in the first place.

### Is CHAP versioned?

Yes. Releases follow [`ROADMAP.md`, Version numbers](./ROADMAP.md#version-numbers),
and a workspace advertises versioned profiles such as `review/1.0`.
From 0.3.0 a workspace refuses methods of profiles it does not
advertise; requests carry a protocol version from 0.4, and parts gain
maturity levels in 0.5. Changes follow the CEP process in
[`GOVERNANCE.md`](./GOVERNANCE.md).

### Will my Core implementation keep working as profiles evolve?

Not guaranteed before 1.0. A minor release may break things, and
its changelog lists each break with a migration. From 1.0 the
specification follows Semantic Versioning, and a breaking change to a
Stable profile takes a new major version
([`ROADMAP.md`, Version numbers](./ROADMAP.md#version-numbers)).

---

## Cultural and organisational fit

### Will a permanent record of overrides feel like surveillance to my team?

It can, and this is worth taking seriously. The same data that helps a high-trust
team learn from its mistakes can be weaponised by a low-trust organisation
against its own people. We have seen this in practice: "overrides by reviewer"
reads very differently to a senior engineer than to a junior one, and reads
differently again to someone who has been quietly working around tools they do
not trust. The protocol surfaces patterns; visibility cuts both ways.

CHAP does not choose what is aggregated, what is shown to whom, or what is done
with the patterns that surface. Those are deployment decisions and they are
consequential. A team that aggregates *overrides-by-reviewer* and uses it for
performance management will hurt itself. A team that aggregates
*overrides-by-tag-and-path* and uses it to improve the agent will help itself.
Same data, different governance, very different culture.

Put plainly: if a team is not ready for that visibility, for reasons
that may be entirely legitimate, including concerns about job design or
autonomy, then CHAP is probably not the right thing to introduce yet. The
trust conversation comes first; the protocol comes after. Aggregations that
look at the *agent's* behaviour are usually safe; aggregations that look at
*individual humans* should be governed by the same controls you would apply to
any other personal performance data, and probably with the same consent and
notification practices. The
[Handbook](./HANDBOOK.md) covers this under deployment patterns and
anti-patterns.

### Isn't this overkill for a small team or a single-tool project?

Possibly. A five-person team building a one-off RAG system for one client,
with no trust boundaries to cross and no compliance obligations to a third
party, can probably get away with logs and conversations. The Core-only path
exists for exactly this case; the whole `reference/core/` implementation is
a single short server file in TypeScript, weekend-buildable.

The same five-person team six months later, when their client asks them to
demonstrate "AI governance" for the client's own customers, will be
retrofitting something CHAP-shaped. The judgement call is when the tax of
structure starts paying for itself. Some signals that it is time:

- You are crossing an organisational boundary, customer, regulator, partner.
- You have more than one human who needs to know what the agent decided last week.
- You are running more than one version of an agent and want to know which version
  produced which output.
- You are accumulating compliance debt that will need to be paid back if you
  ever need to demonstrate AI governance to anyone external.
- You want to learn from override patterns rather than guess at them.

If none of these apply, Core-only or even no CHAP at all is fine. If one or
more apply, the structure starts paying for itself faster than retrofit would.
The twelve worked scenarios in [`IN_PRACTICE.md`](./IN_PRACTICE.md) span solo
developer through GMP-regulated manufacturing precisely so the reader can
locate their own situation.

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
required as the baseline; others may be added by CEP.

### Does CHAP require a specific identity provider?

No. The `identity-oidc` profile works with any OIDC-compliant IdP
that supports `cnf.jwk` or that you can wrap with a DPoP-issuing
shim. The `identity-vc` profile works with any W3C-compliant VC
issuer. There's no vendor dependency at the protocol layer.

### Who can approve, reject, or override a review?

Two conditions, checked in order. First, the actor (`from`) must be a
joined member of the workspace; a Coordinator refuses a review decision
from a non-member with `-32011`. (Every method outside the exemptions in
SPECIFICATION.md §6.3.1 checks membership, the `routing/1.0` methods and
`participant.leave` from 0.3.0.) Second, for a review decision specifically, the
actor must be one of the reviewers the review was addressed to in
`review.request`'s `to` set. A member who was not addressed cannot
decide that review. The `rule` field (`any_one_approves`, `all_approve`,
`quorum:N`) controls *how many* of the addressed reviewers must decide
for the review to terminate; the `to` set controls *who is eligible* to
decide at all. If you want any member to be able to review, address the
request to the workspace itself (`to: "workspace:<id>"`) or to a group
(`to: "group:<id>"`). Either address is a broadcast: any workspace
member becomes eligible, with only the membership floor applying. A
`group:` target does not narrow eligibility to that named group.

Membership is the floor, identity is a separate layer: the
`identity-oidc` and `identity-vc` profiles bind a verified real-world
identity on top of membership, but the membership check applies whether
or not those profiles are active. If you need an approver who has not
yet joined (an escalation target, or an emergency approver), join them
first; that join is itself recorded in the audit log, so there is never
a decision attributed to a non-participant. See SPECIFICATION.md S6.3.1
and profiles/review.md S3.2.

### How does CHAP handle GDPR / right-to-be-forgotten?

Append-only logs and erasure rights are in conflict. CHAP's
pattern (see [`HANDBOOK.md`](./HANDBOOK.md) §8.3 and the
confidentiality limitation in [`SECURITY.md`](./SECURITY.md) §8):

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
- The Coordinator is trusted to order and store the log. A malicious
  Coordinator cannot forge a signed call where `security-signed/1.0`
  is in force; that holds for a verifier who knows the members' keys
  from a source outside the Coordinator, which keeps the list of keys
  itself. The Coordinator still writes the chain and, from 0.3.0, each
  refusal's outcome.
- Individual participant compromise must not destroy the integrity
  of decisions by other participants.
- The log is ordered by acceptance, whatever the sender's clock says.
  From 0.3.0, a signed copy of a recorded refusal is answered with
  that refusal; any other copy of an accepted call is evaluated again
  ([`SECURITY.md`](./SECURITY.md#envelope-id-replay)).

Out of scope: protocol-level confidentiality (use TLS),
denial-of-service resistance (use normal rate-limiting),
side-channel attacks on the signing implementation (use a hardened
library).

---

## Audit and compliance

### Is CHAP suitable for regulated environments?

It can be, with the right profiles and deployment controls. Until
milestone 0.6, authenticate joins in front of the coordinator; no
outside security review has taken place yet
([`SECURITY.md`](./SECURITY.md) §8). A regulated deployment typically
uses `core` + `review` + `identity-oidc` (or `identity-vc`) +
`security-signed` + `audit-scitt`. This gives you:

- Verified participant identity.
- Per-message non-repudiation.
- A hash-chained log that `audit.verify_chain` checks, and SCITT
  statements for a transparency service you connect.
- Structured decision records (approvals, rejections, overrides,
  abstentions, escalations).
- Step-up auth for privileged operations.

Specific regulatory fits (SOX, HIPAA, GDPR, FDA QSR, EU AI Act)
depend on your deployment's controls; the protocol provides the
hooks.

### Does the audit log work offline?

Not yet. CHAP keeps its own log and, with the chain on, its own hash
chain ([SPECIFICATION.md §10.1](./SPECIFICATION.md#101-evidence-chain)),
checked on the Coordinator by `audit.verify_chain`. Neither coordinator
ships an offline verifier; milestone 0.6 adds one, with an export
format. `audit-scitt/1.0` adds an outside witness under the SCITT
architecture ([RFC 9943](https://www.rfc-editor.org/rfc/rfc9943)):
`audit.submit_to_scitt` builds a statement per entry for a submitter
the deployment supplies, which signs and registers it, and
`audit.verify_receipt` calls a deployment verifier. Neither
coordinator runs a transparency service.

### How long should I keep the audit log?

Deployment policy. Typical:

- Operational workspaces: 1-2 years.
- Compliance-relevant: 7 years.
- Healthcare-regulated: 10+ years.

The protocol doesn't impose a retention policy.

### Who runs the SCITT service?

You, a third-party notary, or a federation. SCITT is a standard;
the service is software. Recommended: a third party for production
to avoid the Coordinator being its own notary, but a same-org
service is fine for development. Neither reference coordinator runs
one; you connect it through the submitter option.

---

## Composition and interop

### Can I mix CHAP with my existing app?

Yes. CHAP is a protocol, not a framework. A common pattern:

- Your app's UI calls CHAP methods as if calling any other API.
- Your app's database mirrors the workspace state for UI rendering.
- Your app's audit needs are met by querying the CHAP audit log.
- Your existing identity system feeds OIDC tokens to CHAP.

You don't replace your app; you adopt CHAP for the multi-party-
collaboration parts.

### What if I'm already using MCP?

You keep using it. Two ways CHAP and MCP can compose:

- **MCP-as-tool transport, CHAP-as-governance.** Your agents keep
  calling MCP tools as before; CHAP citations inside artefacts
  reference those tool calls with input/output hashes. The MCP
  traffic stays on the MCP wire. Nothing changes about your existing
  MCP setup. The library helper `wrapMcpToolCall` /
  `wrap_mcp_tool_call` makes recording these citations a one-liner.
- **CHAP-as-MCP-server.** A Coordinator can expose itself as an MCP
  server. Then your existing MCP clients (Claude Desktop, Cursor,
  Claude Code) can drive a CHAP workspace alongside whatever other
  MCP servers they already use.

### What about A2A?

Same story, symmetrically:

- **A2A peer bridged into a workspace.** A2A peers appear inside
  CHAP workspaces as `service:bridge…` participants. The bridge
  mediates; A2A traffic stays on the A2A wire.
  `wrapA2aMessageExchange` / `wrap_a2a_message_exchange` records
  the citation.
- **CHAP-as-A2A-agent.** A Coordinator can present itself as an
  A2A agent. Other A2A orchestrators register it by URL and
  delegate work to it.

### Can I use CHAP without MCP or A2A?

Yes. They're complementary, not required. A CHAP-only deployment
works perfectly well; tools and remote peers are then handled by
whatever mechanism you already use.

### Can CHAP carry binary content?

Envelopes are JSON, so large binary content (images, PDFs, audio) is
referenced by URL or content-addressed hash, not embedded base64.
Small payloads (a kilobyte or two) can be embedded as base64 in
artefacts; anything larger should be stored separately and
referenced.

---

## Governance and licensing

### Who runs CHAP?

The maintainers listed in [`MAINTAINERS.md`](./MAINTAINERS.md) review
and merge changes and run releases, and protocol decisions rest with
the maintainer named there. Substantive changes go through a CEP (CHAP
Enhancement Proposal). [`GOVERNANCE.md`](./GOVERNANCE.md) describes the
structure intended for standards-track promotion.

### Is CHAP free to use?

Yes. The specification text listed in `LICENSE-SPEC.md` is CC BY 4.0;
everything else, including the reference code, is Apache 2.0, which
carries a patent licence from code contributors. A royalty-free patent
commitment for the specification arrives in milestone 0.5.

### Can my company contribute?

Yes. See [`CONTRIBUTING.md`](./CONTRIBUTING.md). Substantive
changes go through CEPs; small changes (clarifications, typos,
schema fixes) are PRs.

### Can my company brand a fork?

Yes, with three constraints (see [`GOVERNANCE.md`](./GOVERNANCE.md) §7):

1. Use a name clearly distinct from CHAP.
2. Don't claim CHAP conformance unless you pass the suite.
3. Submit changes back upstream if they should be part of CHAP.

---

## Implementation specifics

### What language should I implement CHAP in?

Any language with HTTP, JSON, and (for `security-signed`) Ed25519
and JCS libraries. Reference implementations are in TypeScript and
Python. Ports to Go, Rust, Java and C# are welcome.

### Do I need a database for Core?

No. Both coordinators hold state in memory and can save it to a
store you configure, such as the SQLite store each package ships.
Both coordinators answer a call whether or not its write to storage
succeeds; milestone 0.5 decides whether a call must be stored first.
The protocol has no opinion on the database.

### How do I handle high throughput?

Core's bottleneck is typically the audit log. Two patterns:

1. **Single-writer Coordinator** with append-only storage:
   Postgres COPY, or Kafka as the log.
2. **Sharded by workspace**: each Coordinator owns a subset of
   workspaces. Inter-workspace traffic uses the bridge pattern.

Most deployments don't approach the throughput limits before the
audit-volume limits hit first; size for storage.

### What about partial failures and idempotency?

Neither coordinator tracks envelope ids, so a blind retry of a call
that took effect is evaluated again. Where you need a retry to be
idempotent, use the `idempotency_key` parameter on `task.create`: a
redelivered create carrying a key the Coordinator has already seen
returns the original task, with no duplicate and no second audit
entry. `task.create` is the one method that takes a key until
milestone 0.5.

### How do real-time UIs get updates?

Neither reference coordinator pushes. A UI polls `audit.read`,
passing `range.from_seq` as the last `next_seq` it received. An
application that embeds a coordinator can also register an audit
listener (`onAudit` in TypeScript, `add_audit_listener` in Python)
and forward entries itself. [SPECIFICATION.md §14](./SPECIFICATION.md#14-transports)
describes WebSocket, SSE and broker bindings that neither coordinator
implements; milestone 0.5 decides on notifications.

---

## What's next

- To run something: [`START_HERE.md`](./START_HERE.md).
- For the operational guide: [`HANDBOOK.md`](./HANDBOOK.md).
- For the wire-level details: [`core/SPEC.md`](./core/SPEC.md) and
  the profile docs.
- For end-to-end scenarios: [`examples/`](./examples/).
- For composition with adjacent protocols: [`integrations/`](./integrations/).
- For governance and contribution: [`GOVERNANCE.md`](./GOVERNANCE.md) and [`CONTRIBUTING.md`](./CONTRIBUTING.md).
