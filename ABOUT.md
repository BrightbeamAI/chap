# About this repository

This document is the reference companion to the [README](./README.md). README is where you decide whether CHAP is for you and ABOUT tells you what's in the repo, how the protocol relates to the rest of the agent stack, which standards it reuses, and how it evolves.

## Contents

- [The shape of CHAP](#the-shape-of-chap)
- [Where CHAP sits in the stack](#where-chap-sits-in-the-stack)
- [Standards CHAP reuses](#standards-chap-reuses)
- [What CHAP is not](#what-chap-is-not)
- [Status](#status)
- [Conformance](#conformance)
- [Reading paths](#reading-paths)
- [Repository layout](#repository-layout)
- [Getting involved](#getting-involved)
- [Licence](#licence)

## The shape of CHAP

CHAP is two layers, and you adopt them in sequence.

```
┌───────────────────────────────────────────────────────────────────┐
│                          PROFILES                                  │
│  (optional, layered, composable; pick what your workflow needs)    │
│                                                                    │
│  security-signed · audit-scitt · identity-oidc · identity-vc       │
│  review · whisper · deliberation · modes · routing · handoff       │
│  control                                                           │
└───────────────────────────────────────────────────────────────────┘
┌───────────────────────────────────────────────────────────────────┐
│                            CORE                                    │
│  workspace · participant · task · audit                            │
│  JSON-RPC 2.0 envelope · TLS · small spec, weekend-implementable   │
└───────────────────────────────────────────────────────────────────┘
```

Core is enough on its own: a Core deployment is a real, useful CHAP deployment. It does not meet a conformance level by itself; see [Conformance](#conformance). Profiles get added only when their specific capability is needed.

The common adoption path:

1. **Core** for the shared workspace and the audit log.
2. **`review/1.0`** so humans can approve, reject, or override agent output, and the override becomes structured data.
3. **`modes/1.0`** when more than one agent version is in flight; shadow / trial / production stop being labels and become gates.
4. **`identity-oidc/1.0`** when humans need verified identity beyond bearer tokens.
5. **`security-signed/1.0` + `audit-scitt/1.0`** when work crosses a trust boundary (a regulator, a customer dispute, a contractual audit) and approvals need to be non-repudiable.

Each profile is independent. You pay for what you use. The full profile catalogue is in [`profiles/PROFILES.md`](./profiles/PROFILES.md); a single combined reference for Core and every profile is in [`SPECIFICATION.md`](./SPECIFICATION.md).

## Where CHAP sits in the stack

CHAP is the third pillar of the open agent-protocol stack. It composes with MCP and A2A; it does not replace either.

| Protocol  | Owns                                          |
|-----------|-----------------------------------------------|
| [MCP](https://modelcontextprotocol.io)  | Agents talking to tools.        |
| [A2A](https://a2a-protocol.org)         | Agents talking to agents.       |
| **CHAP**                                 | Humans, agents, and services talking together in a shared, auditable workspace. |

**MCP tool calls inside CHAP work.** When an agent in a CHAP workspace invokes an MCP tool to do its job, the call is recorded as a `citation` inside the agent's CHAP artefact, with input and output hashes providing a cryptographic boundary. The CHAP audit log references the MCP transcript without duplicating it. See [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md).

**A2A peers as bridge participants.** When work crosses an organisational boundary, the remote A2A peer is represented inside the CHAP workspace as a `service:bridge…` participant. The bridge participant signs CHAP envelopes on behalf of the remote peer; the A2A traffic itself does not cross the CHAP wire. See [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md).

**CHAP-as-MCP-server and CHAP-as-A2A-agent.** A Coordinator can present itself as an MCP server or an A2A agent, with every CHAP method exposed as a tool or skill. Claude Desktop, Cursor, Claude Code, or any A2A-aware orchestrator can then drive a workspace directly. The transport adapter packages are [`packages/coordinator-mcp/`](./packages/coordinator-mcp/) and [`packages/coordinator-a2a/`](./packages/coordinator-a2a/); the runnable reference servers are under [`reference/mcp-server-{ts,py}/`](./reference/) and [`reference/a2a-server-{ts,py}/`](./reference/). Library helpers for the inward citation pattern ship with the Coordinator packages themselves, as `wrapMcpToolCall` / `wrapA2aMessageExchange` in TypeScript and `wrap_mcp_tool_call` / `wrap_a2a_message_exchange` in `chap_coordinator.transports.wrap`. Five-minute walkthroughs: [`examples/drive-chap-from-claude-desktop.md`](./examples/drive-chap-from-claude-desktop.md) and [`examples/drive-chap-from-an-a2a-orchestrator.md`](./examples/drive-chap-from-an-a2a-orchestrator.md).

**Framework bridges.** Five thin Python adapters connect a framework's own human-in-the-loop mechanism to CHAP's `review` and `decide` methods, so an approval, edit, or denial in the framework lands on the audit chain as `decide.approve`, `decide.override`, or `decide.reject`: [`chap-langgraph`](./packages/chap-langgraph/) for LangGraph, [`chap-pydantic-ai`](./packages/chap-pydantic-ai/) for Pydantic AI, [`chap-ag2`](./packages/chap-ag2/) for AG2 / AutoGen, [`chap-llama-index`](./packages/chap-llama-index/) for LlamaIndex Workflows, and [`chap-google-adk`](./packages/chap-google-adk/) for Google ADK. Each bridge carries its own examples and tests, and each framework is an optional dependency.

## Standards CHAP reuses

CHAP defers to existing standards wherever they exist. The only protocol-level things it introduces are its Core methods, the override envelope shape, and the profile binding model. Everything else is composition.

| Need                       | Standard                                          |
|----------------------------|---------------------------------------------------|
| Envelope                   | JSON-RPC 2.0                                      |
| Canonical bytes            | RFC 8785 (JCS)                                    |
| Override diff              | RFC 6902 (JSON Patch)                             |
| Human identity             | OIDC + `cnf.jwk` (RFC 7800) or DPoP (RFC 9449)    |
| Richer identity            | W3C Verifiable Credentials 2.0                    |
| Service identity           | SPIFFE / SPIRE                                    |
| Transparency log           | IETF SCITT architecture (RFC 9943), COSE (RFC 9052) |
| Federation                 | ActivityPub (a possible mapping, which no profile defines) |
| Provisioning               | SCIM 2.0 (a possible mapping, which no profile defines) |
| Conformance attestations   | in-toto                                           |

The full mapping with rationale is in [`RELATIONSHIP-TO-OTHER-STANDARDS.md`](./RELATIONSHIP-TO-OTHER-STANDARDS.md).

## What CHAP is not

To save you time:

- **Not a framework for building agents.** Use LangGraph, AutoGen, CrewAI, or your own. CHAP is the wire between them and the humans.
- **Not a workflow engine.** Use Temporal, Airflow, or Argo for durable workflow execution. CHAP records what happened.
- **Not a new identity protocol.** CHAP reuses OIDC, OAuth 2.0, and W3C Verifiable Credentials.
- **Not a new transparency log.** CHAP's `audit-scitt/1.0` profile uses IETF SCITT.
- **Not a new RPC.** CHAP envelopes are JSON-RPC 2.0.
- **Not vendor-locked.** Anyone may implement it: the specification text is CC BY 4.0 and the code Apache 2.0.

CHAP also deliberately does **not** define:

- **A claim or evidence taxonomy.** Whether artefacts carry typed claims, arguments, or domain-specific schemes is for deployments and profiles to decide.
- **A temporal model beyond `produced_at`.** Validity windows, effectivity intervals, and subject-time-versus-statement-time semantics belong in the artefact content shape or in a profile.
- **Confidence calibration.** `routing_hints.confidence` is what the model said; CHAP does not interpret it.
- **What evidence is sufficient for any regulator.** CHAP produces a verifiable record; sufficiency under any specific audit or conformity regime is the deploying organisation's determination.

CHAP **is** the small set of common verbs that every team rebuilds in their own application layer when they put humans and agents on the same work: delegate, accept, decline, complete, review, approve, override, abstain, escalate, whisper, hand off, pause, resume. Standardising those verbs gives you interoperable tools, portable audits, and learning data that wasn't structured before.

## Status

CHAP is a working draft (0.3). Concretely:

- Two reference coordinators, in TypeScript at [`packages/coordinator/`](./packages/coordinator/) and Python at [`packages/coordinator-py/`](./packages/coordinator-py/), implement the same methods, and a differential fuzzer checks that they answer and log alike. The conformance harness covers Core and `review/1.0` and runs against the Python coordinator and a standalone TypeScript server.
- Stable enough for experimentation and early production pilots, after reading the known limitations in [`SECURITY.md`](./SECURITY.md) §8.
- Neither reference coordinator meets a conformance level: Minimal needs `participant.describe`, and Recommended needs `capture.append` and `audit.verify`, all specified and not yet built. Milestone 0.4 replaces the levels with conformance by profile.
- Before 1.0 a minor release may break things, and its changelog lists each break with a migration. From 1.0 the specification follows Semantic Versioning ([`ROADMAP.md`, Version numbers](./ROADMAP.md#version-numbers)).
- Profile surfaces evolve faster than Core. Core is more stable; profile API surface should be expected to change.
- Deployments needing strict stability guarantees should wait for 1.0.

The full Status statement is in [`SPECIFICATION.md`](./SPECIFICATION.md#status-of-this-document).

## Conformance

Implementing every Core method and the wire format does not meet a level under the 0.3 text: the **Minimal** level in [`SPECIFICATION.md`](./SPECIFICATION.md#17-conformance) §17 also needs Ed25519 signing, the hash-chained log, `participant.describe` and three `review/1.0` methods. Attest exactly what you implement and which harness tests pass.

Conformance levels in 0.3 are **Minimal** and **Recommended**, with **Full** planned. Full needs an interop run against an independently authored implementation (the two references here share authorship). A common base for production deployments is Core, `review/1.0` and `modes/1.0`.

The harness can write an [in-toto attestation](https://github.com/in-toto/attestation) (`--attest`). See [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md) and [`conformance/test-vectors.md`](./conformance/test-vectors.md).

## Reading paths

| If you are…                              | Read in this order                                                              |
|-------------------------------------------|---------------------------------------------------------------------------------|
| Evaluating whether CHAP fits             | [`README.md`](./README.md) → [`IN_PRACTICE.md`](./IN_PRACTICE.md) → [`HANDBOOK.md`](./HANDBOOK.md) → [`FAQ.md`](./FAQ.md) |
| Looking for your use case                | [`IN_PRACTICE.md`](./IN_PRACTICE.md): twelve worked scenarios |
| Starting a project on your own agent     | `npx create-chap-app` ([`packages/create-chap-app/`](./packages/create-chap-app/)): a desk, one profile setting, `diff-profiles`, and [`docs/profile-explorer.md`](./docs/profile-explorer.md) for what each profile changes |
| Seeing it run                            | [`START_HERE.md`](./START_HERE.md) (one command, a real decision) → [`demo/index.html`](./demo/index.html) (static, offline) → [`reference/playground/`](./reference/playground/) (TypeScript, two humans + local LLM) → [`reference/python/`](./reference/python/) (Python; every profile) |
| Implementing in TypeScript               | [`core/SPEC.md`](./core/SPEC.md) → [`packages/coordinator/`](./packages/coordinator/) → [`reference/core/`](./reference/core/) (minimal Core, weekend-buildable) or [`reference/core-plus-review/`](./reference/core-plus-review/) (Core + review) |
| Implementing in Python                   | [`core/SPEC.md`](./core/SPEC.md) → [`packages/coordinator-py/`](./packages/coordinator-py/) → [`reference/python/`](./reference/python/) |
| Adding a profile                         | [`profiles/PROFILES.md`](./profiles/PROFILES.md) → the specific profile spec |
| Reviewing the design                     | [`ARCHITECTURE.md`](./ARCHITECTURE.md) → [`SECURITY.md`](./SECURITY.md) → [`RELATIONSHIP-TO-OTHER-STANDARDS.md`](./RELATIONSHIP-TO-OTHER-STANDARDS.md) |
| Operating in production                  | [`HANDBOOK.md`](./HANDBOOK.md) → [`integrations/CHAP-deployment-patterns.md`](./integrations/CHAP-deployment-patterns.md) |
| Composing with MCP or A2A                | [`integrations/CHAP-with-MCP.md`](./integrations/CHAP-with-MCP.md) · [`integrations/CHAP-with-A2A.md`](./integrations/CHAP-with-A2A.md) |
| Verifying conformance                    | [`conformance/conformance-checklist.md`](./conformance/conformance-checklist.md) · [`conformance/test-vectors.md`](./conformance/test-vectors.md) |
| Looking up a term                        | [`GLOSSARY.md`](./GLOSSARY.md) |
| Contributing                             | [`CONTRIBUTING.md`](./CONTRIBUTING.md) → [`GOVERNANCE.md`](./GOVERNANCE.md) |

For a single end-to-end document combining Core and every profile into one cross-referenced reference, see [`SPECIFICATION.md`](./SPECIFICATION.md).

## Repository layout

The repo has four kinds of content: top-level Markdown docs (landing, reference, governance), normative specs (Core and profiles, plus schemas), runnable code (reference implementations, packages, demo, conformance), and supporting material (integrations, examples, diagrams).

```
chap/
│
├── README.md                            Landing page.
├── START_HERE.md                        One command to a first recorded decision.
├── ABOUT.md                             You are here.
├── IN_PRACTICE.md                       Twelve real-world scenarios.
├── HANDBOOK.md                          Operating CHAP in production.
├── FAQ.md                               Common questions.
├── GLOSSARY.md                          Term reference.
│
├── SPECIFICATION.md                     Combined Core + every profile, single doc.
├── ARCHITECTURE.md                      Design rationale.
├── SECURITY.md                          Threat model.
├── RELATIONSHIP-TO-OTHER-STANDARDS.md   How CHAP reuses JSON-RPC, OIDC, SCITT, etc.
│
├── CONTRIBUTING.md                      How to contribute.
├── GOVERNANCE.md                        How the protocol evolves (the CEP process).
├── CODE_OF_CONDUCT.md                   Contributor Covenant 2.1.
├── CHANGELOG.md                         Release notes.
├── MAINTAINERS.md                       Who reviews what.
├── IMPLEMENTATIONS.md                   Known implementations and their conformance.
├── LICENSE                              Apache 2.0, for every file LICENSE-SPEC.md does not list.
├── LICENSE-SPEC.md                      CC BY 4.0, for the specification text.
│
├── core/
│   └── SPEC.md                          Core specification: seven methods.
│
├── profiles/                            Optional, composable extensions.
│   ├── PROFILES.md                      Catalogue and composition rules.
│   ├── review.md                        Approve / reject / override.
│   ├── modes.md                         Shadow / trial / production gates.
│   ├── handoff.md                       Shift handoff with context.
│   ├── routing.md                       Criticality and risk hints.
│   ├── deliberation.md                  Multi-reviewer voting.
│   ├── whisper.md                       Typed clarifying questions.
│   ├── control.md                       Pause, resume, supersede.
│   ├── identity-oidc.md                 OIDC-bound participants.
│   ├── identity-vc.md                   W3C Verifiable Credentials.
│   ├── security-signed.md               Non-repudiable approvals.
│   └── audit-scitt.md                   Transparency-log anchored audit.
│
├── schemas/                             Normative JSON Schemas.
│   ├── core/                            Envelope, workspace, participant, task.
│   └── profiles/                        Per-profile schemas.
│
├── reference/                           Reference implementations.
│   ├── core/                            TypeScript: minimal Core, weekend-buildable.
│   ├── core-plus-review/                TypeScript: Core + review/1.0 + override analyser.
│   ├── playground/                      TypeScript: runnable two-human + local LLM demo.
│   ├── python/                          Python: HTTP server + demo client + analytics.
│   ├── mcp-server-ts/                   MCP stdio server (TS) wrapping a Coordinator.
│   ├── mcp-server-py/                   MCP stdio server (Python) wrapping a Coordinator.
│   ├── a2a-server-ts/                   A2A HTTP server (TS, Express).
│   └── a2a-server-py/                   A2A HTTP server (Python, FastAPI).
│
├── packages/
│   ├── coordinator/                     @brightbeamai/chap-coordinator (TypeScript): protocol as a library.
│   ├── coordinator-py/                  chap-coordinator (Python): Core + every profile.
│   ├── coordinator-mcp/                 @brightbeamai/chap-coordinator-mcp: MCP transport adapter.
│   ├── coordinator-a2a/                 @brightbeamai/chap-coordinator-a2a: A2A transport adapter.
│   ├── chap-langgraph/                  Bridge for LangGraph (Python).
│   ├── chap-pydantic-ai/                Bridge for Pydantic AI (Python).
│   ├── chap-ag2/                        Bridge for AG2 / AutoGen (Python).
│   ├── chap-llama-index/                Bridge for LlamaIndex Workflows (Python).
│   ├── chap-google-adk/                 Bridge for Google ADK (Python).
│   ├── chap-analytics/                  Reads logs back as tables and charts (Python).
│   └── create-chap-app/                 npx create-chap-app: project templates with a desk and diff-profiles.
│
├── conformance/                         Test suite for implementers.
│   ├── conformance-checklist.md
│   ├── test-vectors.md
│   └── harness/                         Runnable harness, in-toto attestations.
│
├── integrations/                        Composing with adjacent standards.
│   ├── CHAP-with-MCP.md
│   ├── CHAP-with-A2A.md
│   ├── CHAP-with-OIDC-OAuth2.md
│   └── CHAP-deployment-patterns.md
│
├── examples/                            Worked end-to-end envelope sequences.
│   ├── 00-five-minute-start.md
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
├── start-here/                          The runnable starter: review desk and helper.
│
├── scenarios/                           Runnable domain narratives on Core.
│
├── ceps/                                CHAP Enhancement Proposals.
│
├── demo/                                Static single-file HTML walkthrough.
│   └── index.html
│
├── docs/                                Supporting assets for the docs themselves.
│   ├── img/                             SVG and PNG diagrams used in README.
│   └── casts/                           vhs tape scripts for demo GIFs.
│
└── diagrams/                            Mermaid source for spec figures.
```

## Getting involved

CHAP is developed in the open. Issues, discussions, and proposed changes are welcome.

- **Reporting an issue.** Use the issue tracker for spec ambiguities, schema bugs, or interoperability problems. See [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- **Proposing a change.** Substantive changes go through the CEP (CHAP Enhancement Proposal) process described in [`GOVERNANCE.md`](./GOVERNANCE.md).
- **Implementing the protocol.** The reference implementations in [`reference/`](./reference/) are starting points. Run the conformance suite to validate your implementation.
- **Security disclosures.** Coordinated disclosure procedure in [`SECURITY.md`](./SECURITY.md).

## Licence

CC BY 4.0 for the specification text listed in `LICENSE-SPEC.md`, and [Apache 2.0](./LICENSE) for everything else, including all code. A written royalty-free patent commitment is planned for milestone 0.5.
