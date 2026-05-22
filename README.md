# CHAP — The Collaborative Human-Agent Protocol

**CHAP is the open standard for multi-human, multi-agent collaboration.**
It defines the wire format, methods, identity bindings, audit
semantics, and operational primitives required to put humans,
agents, and services in a shared workspace and have them produce
verifiable, structured, auditable work together.

CHAP is the third pillar of the open agent-protocol stack:

| Protocol  | Owns                                          |
|-----------|-----------------------------------------------|
| [MCP](https://modelcontextprotocol.io)  | Agents talking to **tools**.        |
| [A2A](https://a2a.dev)                  | Agents talking to **agents**.       |
| **CHAP**                                 | Humans, agents, and services talking **together** in a shared, auditable workspace. |

CHAP is licensed CC-BY 4.0 (specification) and Apache 2.0 (code). It
is implementable royalty-free, in any language, in any deployment.

> **See it first.** [Open `demo/index.html`](./demo/index.html) for
> an interactive walkthrough that tells CHAP's story in five minutes —
> the problem, the analogy, the protocol, the override-as-data
> dividend. Single self-contained HTML file; works offline.

---

## Table of contents

1. [What CHAP gives you](#what-chap-gives-you)
2. [Core + Profiles](#core--profiles)
3. [5-minute start](#5-minute-start)
4. [Reading paths](#reading-paths)
5. [Composition with MCP and A2A](#composition-with-mcp-and-a2a)
6. [What CHAP is not](#what-chap-is-not)
7. [Standards reused](#standards-reused)
8. [Conformance and adoption](#conformance-and-adoption)
9. [Repository layout](#repository-layout)
10. [Getting involved](#getting-involved)

---

## What CHAP gives you

For teams shipping any system where humans and agents share work:

- **A common wire format.** Every message — task delegation, review
  request, override, abstention, handoff — has a defined shape, so
  tools, dashboards, and audits work across implementations.
- **Structured override capture.** Every human edit to an agent's
  output carries a typed diff, rationale, and tags. Your audit log
  becomes a tuning dataset as a side effect of normal work.
- **Typed abstention.** "I shouldn't decide this" is a first-class
  signal, not silence. Abstention rates tune the boundary between
  what an agent or role handles and what gets escalated.
- **A mode promotion ladder.** New agents move from `shadow` to
  `trial` to `production` through protocol-enforced gates.
- **Portable, verifiable audit.** Every accepted envelope is
  preserved. With the [`audit-scitt`](./profiles/audit-scitt.md)
  profile, audits are cryptographically verifiable offline by any
  third party.
- **Verified identity that you didn't have to invent.** The
  identity profiles bind to OIDC or W3C Verifiable Credentials —
  no bespoke identity layer.
- **Clean composition with the rest of the agent stack.** MCP tool
  calls are *cited* inside CHAP artefacts; A2A peers appear as
  bridge participants. No re-implementation of either protocol.

---

## Core + Profiles

CHAP is two layers, and you adopt them in sequence:

```
┌───────────────────────────────────────────────────────────────────┐
│                          PROFILES                                  │
│  (optional, layered, composable — pick what your workflow needs)   │
│                                                                    │
│  security-signed · audit-scitt · identity-oidc · identity-vc       │
│  review · whisper · deliberation · modes · routing · handoff       │
│  control                                                           │
└───────────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────────┐
│                            CORE                                    │
│  workspace · participant · task · audit · 7 methods                │
│  JSON-RPC 2.0 envelope · TLS · weekend-implementable               │
└───────────────────────────────────────────────────────────────────┘
```

**Core is enough on its own.** A Core deployment is a real,
useful, conformant CHAP deployment. Add profiles only when their
specific capability is needed.

The most common adoption path:

1. **Core** for the shared workspace and the audit log.
2. **`review`** so humans can approve, reject, or override agent
   output — and so override-as-learning-signal becomes free.
3. **`modes`** for safe rollout of new agents.
4. **`identity-oidc`** when you need verified human identity beyond
   bearer tokens.
5. **`security-signed`** + **`audit-scitt`** when you need
   cryptographic non-repudiation.

Each profile is independent. You pay for what you use.

---

## 5-minute start

```bash
git clone <repo>
cd chap-protocol/reference/core
npm install
npm run start:demo
```

In another terminal:

```bash
curl -sS -X POST http://localhost:8080/chap \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "participant.join",
    "params": {
      "workspace":    "wsp_demo",
      "from":         "human:alice@example.org",
      "to":           "service:coordinator@example.org",
      "ts":           "2026-05-17T09:00:00Z",
      "type":         "human",
      "display_name": "Alice",
      "role":         "reviewer"
    }
  }'
```

That's one CHAP message. The full walkthrough — joining, delegating
a task, completing it, reading the audit log — is in
[`examples/00-five-minute-start.md`](./examples/00-five-minute-start.md).

---

## Reading paths

| Audience                                  | Read in this order                                                              |
|-------------------------------------------|---------------------------------------------------------------------------------|
| **Evaluating** whether CHAP fits           | This README → [`HANDBOOK.md`](./HANDBOOK.md) → [`FAQ.md`](./FAQ.md)              |
| **Implementing Core**                     | [`core/SPEC.md`](./core/SPEC.md) → [`reference/core/`](./reference/core/) → [`schemas/core/`](./schemas/core/) |
| **Adding a profile**                      | [`profiles/PROFILES.md`](./profiles/PROFILES.md) → the specific profile spec    |
| **Reviewing the design**                  | [`ARCHITECTURE.md`](./ARCHITECTURE.md) → [`SECURITY.md`](./SECURITY.md) → [`RELATIONSHIP-TO-OTHER-STANDARDS.md`](./RELATIONSHIP-TO-OTHER-STANDARDS.md) |
| **Operating in production**               | [`HANDBOOK.md`](./HANDBOOK.md) → [`integrations/CHAP-deployment-patterns.md`](./integrations/CHAP-deployment-patterns.md) |
| **Composing with MCP or A2A**             | [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md) · [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md) |
| **Verifying conformance**                 | [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md) · [`conformance/test-vectors.md`](./conformance/test-vectors.md) |
| **Looking up a term**                     | [`GLOSSARY.md`](./GLOSSARY.md)                                                  |
| **Contributing**                          | [`CONTRIBUTING.md`](./CONTRIBUTING.md) → [`GOVERNANCE.md`](./GOVERNANCE.md)     |

For a single end-to-end document combining Core and every profile
into one cross-referenced reference, see [`SPECIFICATION.md`](./SPECIFICATION.md).

---

## Composition with MCP and A2A

CHAP, MCP, and A2A are designed to compose without conflict.

**MCP tool calls inside CHAP work.** When an agent in a CHAP workspace
invokes an MCP tool to do its job, the call is recorded as a
`citation` inside the agent's CHAP artefact, with input and output
hashes providing a cryptographic boundary. The CHAP audit log
references — but does not duplicate — the MCP transcript. See
[`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md).

**A2A peers as bridge participants.** When work crosses an
organisational boundary, the remote A2A peer is represented inside
the CHAP workspace as a `service:bridge…` participant. The bridge
participant signs CHAP envelopes on behalf of the remote peer;
the A2A traffic itself does not cross the CHAP wire. See
[`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

---

## What CHAP is not

To save you time:

- **Not a framework for building agents.** Use LangGraph, AutoGen,
  CrewAI, or your own. CHAP is the wire between them and the humans.
- **Not a workflow engine.** Use Temporal, Airflow, or Argo for
  durable workflow execution. CHAP records what happened.
- **Not a new identity protocol.** CHAP reuses OIDC, OAuth 2.0, and
  W3C Verifiable Credentials.
- **Not a new transparency log.** CHAP's `audit-scitt` profile uses
  IETF SCITT.
- **Not a new RPC.** CHAP envelopes are JSON-RPC 2.0.
- **Not vendor-locked.** Multi-implementation by construction;
  Apache 2.0 + CC-BY 4.0 throughout.

CHAP **is** the small set of common verbs that every team rebuilds
in their own app layer when they put humans and agents on the same
work — delegate, accept, decline, complete, review, approve,
override, abstain, escalate, whisper, hand off, pause, resume.
Standardising those verbs gives you interoperable tools, portable
audits, and learning data that wasn't structured before.

---

## Standards reused

CHAP defers to existing standards wherever they exist. The full
mapping is in [`RELATIONSHIP-TO-OTHER-STANDARDS.md`](./RELATIONSHIP-TO-OTHER-STANDARDS.md).
Highlights:

| Need                       | Standard                                          |
|----------------------------|---------------------------------------------------|
| Envelope                   | JSON-RPC 2.0                                      |
| Canonical bytes            | RFC 8785 (JCS)                                    |
| Override diff              | RFC 6902 (JSON Patch)                             |
| Human identity             | OIDC + `cnf.jwk` (RFC 7800) or DPoP (RFC 9449)    |
| Richer identity            | W3C Verifiable Credentials 2.0                    |
| Service identity           | SPIFFE / SPIRE                                    |
| Transparency log           | IETF SCITT (COSE, RFC 9052)                       |
| Federation                 | ActivityPub (optional binding)                    |
| Provisioning               | SCIM 2.0 (optional binding)                       |
| Conformance attestations   | in-toto                                           |

The only protocol-level things CHAP introduces are the methods and
the override-with-rationale shape. Everything else is composition.

---

## Conformance and adoption

An implementation is **CHAP-conformant** if it implements every
Core method and conforms to the wire format. Profile conformance
is declared separately, one attestation per profile. See
[`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md).

Conformance attestations are published as
[in-toto attestations](https://github.com/in-toto/attestation) and
linked from the implementation registry. The expected base
conformance — the level most production deployments will claim —
is **Core + `review` + `modes`**.

---

## Repository layout

```
chap-protocol/
├── README.md                              You are here.
├── HANDBOOK.md                            Practical guide to running CHAP.
├── FAQ.md                                 Common questions.
│
├── core/
│   └── SPEC.md                            Core specification.
├── profiles/
│   ├── PROFILES.md                        Profile catalogue.
│   ├── review.md
│   ├── whisper.md
│   ├── deliberation.md
│   ├── modes.md
│   ├── handoff.md
│   ├── control.md
│   ├── security-signed.md
│   ├── audit-scitt.md
│   ├── identity-oidc.md
│   └── identity-vc.md
│
├── SPECIFICATION.md                       Single-document combined reference.
├── ARCHITECTURE.md                        Design rationale.
├── SECURITY.md                            Threat model and security policy.
├── GLOSSARY.md                            Term reference.
├── RELATIONSHIP-TO-OTHER-STANDARDS.md     Standards mapping.
├── GOVERNANCE.md                          How the protocol evolves.
├── CONTRIBUTING.md                        How to contribute.
├── CHANGELOG.md                           Release notes.
├── LICENSE                                Apache 2.0 + CC-BY 4.0.
│
├── examples/                              Worked end-to-end scenarios.
│   ├── 00-five-minute-start.md            Onramp.
│   ├── 01-discovery.md
│   ├── 02-task-delegation.md
│   ├── 03-review-and-approve.md
│   ├── 04-abstain-and-escalate.md
│   ├── 05-override-capture.md
│   ├── 06-whisper-prompt.md
│   ├── 07-handoff-shift-change.md
│   ├── 08-multi-human-deliberation.md
│   ├── 09-pause-resume-rollback.md
│   └── 10-end-to-end-workflow.md
│
├── demo/                                  Interactive HTML demo.
│   └── index.html                         Single-file walkthrough.
│
├── integrations/                          Composition with adjacent standards.
│   ├── CHAP-with-MCP.md
│   ├── CHAP-with-A2A.md
│   ├── CHAP-with-OIDC-OAuth2.md
│   └── CHAP-deployment-patterns.md
│
├── schemas/                               JSON Schema definitions.
│   ├── core/
│   └── profiles/
│
├── reference/                             Reference implementations.
│   ├── core/                              Minimal Core (weekend-buildable).
│   └── core-plus-review/                  Core + Review profile + override analyser.
│
├── conformance/                           Conformance suite.
│   ├── conformance-checklist.md
│   ├── test-vectors.md
│   └── harness/                           Runnable test harness with in-toto attestation.
│
└── diagrams/                              Mermaid source for spec figures.
```

---

## Getting involved

CHAP is developed in the open. Issues, discussions, and proposed
changes are welcome.

- **Reporting an issue.** Use the issue tracker for spec ambiguities,
  schema bugs, or interoperability problems. See [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- **Proposing a change.** Substantive changes go through the HEP
  (CHAP Enhancement Proposal) process described in [`GOVERNANCE.md`](./GOVERNANCE.md).
- **Implementing the protocol.** The reference implementations in
  [`reference/`](./reference/) are starting points. Run the
  conformance suite to validate your implementation.
- **Security disclosures.** Coordinated disclosure procedure in
  [`SECURITY.md`](./SECURITY.md).

License: [Apache 2.0](./LICENSE) (code) · CC-BY 4.0 (specification).
