# CHAP Architecture

This document is **informative**. It explains the design choices behind CHAP,
how the primitives fit together, and what deployment topologies are practical.
For the normative wire format, see [SPECIFICATION.md](./SPECIFICATION.md).

---

## 1. The protocol stack

CHAP sits alongside MCP and A2A. Each protocol owns a single concern.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "22px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#1a1a1c",
    "secondaryColor": "#f5f5f7",
    "tertiaryColor": "#FFF8F2"
  },
  "flowchart": { "curve": "linear", "nodeSpacing": 70, "rankSpacing": 90 }
}}%%
flowchart LR
    classDef human  fill:#FFF8F2,stroke:#1a1a1c,stroke-width:2px,color:#1a1a1c
    classDef agent  fill:#FFE7DD,stroke:#EA4700,stroke-width:2px,color:#5a1500
    classDef coord  fill:#EA4700,stroke:#C73D00,stroke-width:2px,color:#ffffff
    classDef tool   fill:#E8F1ED,stroke:#1f5b39,stroke-width:2px,color:#0a3a1c
    classDef peer   fill:#EDE0F2,stroke:#6a3d8a,stroke-width:2px,color:#3b0e63

    H1["Human<br/>Reviewer"]:::human
    H2["Human<br/>Approver"]:::human
    A1["Agent<br/>Drafter"]:::agent
    C["Coordinator<br/>(CHAP)"]:::coord
    T["Tool Server<br/>(MCP)"]:::tool
    P["Peer Agent<br/>(A2A)"]:::peer

    H1 ===|CHAP| C
    H2 ===|CHAP| C
    A1 ===|CHAP| C
    A1 -.->|MCP| T
    A1 -.->|A2A| P
```

**Reading the diagram.** Solid lines are CHAP. Dotted lines are MCP and A2A.
CHAP sits in the middle, holding the workspace; MCP and A2A radiate outward
to tools and external agents respectively.

---

## 2. Core primitives

CHAP has a small set of primitives, related as follows.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "20px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#FFF8F2",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#4f4f52"
  }
}}%%
classDiagram
    direction TB
    class Workspace {
      +id
      +state
      +mode
      +members
      +policy_uri
      +evidence_head
    }
    class Participant {
      +uri
      +type
      +jwks
      +capabilities
      +scopes
    }
    class Task {
      +id
      +kind
      +state
      +mode
      +assignee
      +delegator
    }
    class Artefact {
      +id
      +kind
      +content
      +citations
      +content_hash
    }
    class EvidenceEntry {
      +seq
      +envelope_hash
      +prev_hash
      +sig
    }
    class Message {
      +id
      +ts
      +from
      +to
      +method
      +evidence
    }

    Workspace "1" --> "*" Participant : members
    Workspace "1" --> "*" Task : holds
    Workspace "1" --> "*" EvidenceEntry : append-only log
    Task "1" --> "*" Artefact : produces
    Message "1" --> "1" EvidenceEntry : becomes
    Participant "1" --> "*" Message : sends
```

**The contract.** Every Message becomes exactly one EvidenceEntry. Tasks
live inside Workspaces. Artefacts are produced by Tasks. Participants
send Messages. There is exactly one EvidenceEntry per accepted Message,
and the chain is per-Workspace.

---

## 3. Task lifecycle

A Task is the unit of work. Its state machine is small and explicit.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "20px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#FFE7DD",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#EA4700",
    "secondaryColor": "#FFF8F2",
    "secondaryTextColor": "#1a1a1c",
    "secondaryBorderColor": "#1a1a1c",
    "tertiaryColor": "#FFF8F2",
    "tertiaryTextColor": "#1a1a1c",
    "tertiaryBorderColor": "#1a1a1c",
    "lineColor": "#4f4f52"
  }
}}%%
stateDiagram-v2
    direction TB
    [*] --> Created : task.assign
    Created --> Accepted : accept
    Created --> Declined : decline
    Accepted --> InProgress : start
    InProgress --> ReviewRequested : review
    InProgress --> Completed : complete
    ReviewRequested --> Completed : approve
    ReviewRequested --> InProgress : reject
    InProgress --> Abstained : abstain
    InProgress --> Escalated : escalate
    InProgress --> Cancelled : cancel
    Accepted --> Cancelled : cancel
    InProgress --> Superseded : supersede
    Completed --> [*]
    Declined --> [*]
    Cancelled --> [*]
    Abstained --> [*]
    Escalated --> [*]
    Superseded --> [*]
```

**Things to note.**

- `Declined` is terminal but **non-blocking** — the task can be reassigned
  by a new `task.assign`. The new assignment produces a new task ID.
- `Abstained` and `Escalated` are terminal **for this assignee** but
  trigger a new assignment to the escalation target.
- `Superseded` is the protocol's "redo" — the superseded task remains in
  the evidence chain, linked to its successor.

---

## 4. The evidence chain

CHAP's audit guarantee is a per-workspace hash-linked log.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "20px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#4f4f52"
  },
  "flowchart": { "curve": "linear", "nodeSpacing": 50, "rankSpacing": 50, "padding": 16 }
}}%%
flowchart TB
    classDef entry  fill:#FFF8F2,stroke:#1a1a1c,stroke-width:2px,color:#1a1a1c
    classDef chkpt  fill:#EA4700,stroke:#C73D00,stroke-width:2.5px,color:#ffffff
    classDef anchor fill:#FFE7DD,stroke:#EA4700,stroke-width:2px,color:#5a1500

    E0["<b>entry 0</b> · genesis"]:::entry
    E1["<b>entry 1</b> · task.assign"]:::entry
    E2["<b>entry 2</b> · task.accept"]:::entry
    E3["<b>entry 3</b> · task.complete"]:::entry
    E4["<b>entry 4</b> · review.request"]:::entry
    E5["<b>entry 5</b> · decide.approve"]:::entry
    CK["<b>checkpoint</b><br/>every 1000"]:::chkpt
    AN["<b>external anchor</b>"]:::anchor

    E0 -->|prev_hash| E1
    E1 -->|prev_hash| E2
    E2 -->|prev_hash| E3
    E3 -->|prev_hash| E4
    E4 -->|prev_hash| E5
    E5 -. signs head .-> CK
    CK -. publishes to .-> AN
```

**Verification cost.** Replaying the entire chain is O(n) in entries and
fully parallelisable past any checkpoint. In practice, verifiers replay
only the segment of interest (typically a single task's worth of entries,
~10–50) and trust the latest checkpoint for the rest.

---

## 5. Mode-aware routing

Modes are an envelope-level concern. The Coordinator enforces them on
every dispatch.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "22px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#1a1a1c"
  },
  "flowchart": { "curve": "linear", "nodeSpacing": 70, "rankSpacing": 90, "padding": 22 }
}}%%
flowchart TB
    classDef mode  fill:#FFE7DD,stroke:#EA4700,stroke-width:2px,color:#5a1500
    classDef ok    fill:#E8F1ED,stroke:#1f5b39,stroke-width:2px,color:#0a3a1c
    classDef block fill:#1a1a1c,stroke:#1a1a1c,stroke-width:2px,color:#ffffff

    IN["task.assign<br/>mode = X"]:::mode
    CHK{"X ≤ workspace<br/>mode_ceiling?"}
    YES["dispatch"]:::ok
    NO["reject<br/>-32501<br/>mode_ceiling_exceeded"]:::block
    OBS{"X = shadow?"}
    ROUTE1["dispatch to assignee<br/>+ shadow_observers only"]:::ok
    ROUTE2["dispatch to assignee<br/>(normal routing)"]:::ok

    IN --> CHK
    CHK -- no  --> NO
    CHK -- yes --> OBS
    OBS -- yes --> ROUTE1
    OBS -- no  --> ROUTE2
```

**Promotion.** Moving a workspace from `trial` to `production` is a
privileged operation. It requires step-up authentication and matches
against an explicit policy entry. The transition is recorded as a
first-class evidence entry so promotion history is auditable.

---

## 6. Override capture

Overrides are where the protocol earns its keep. A human who modifies
an agent's draft produces a structured record — diff, rationale, tags —
that is immediately available for downstream learning.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "22px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#1a1a1c",
    "actorBkg": "#EA4700",
    "actorTextColor": "#ffffff",
    "actorBorder": "#C73D00",
    "labelTextColor": "#1a1a1c",
    "noteBkgColor": "#FFF8F2",
    "noteTextColor": "#1a1a1c",
    "noteBorderColor": "#EA4700"
  }
}}%%
sequenceDiagram
    autonumber
    participant A as Agent (Drafter)
    participant C as Coordinator
    participant H as Human (Reviewer)

    A->>C: task.complete (draft artefact)
    C->>H: review.request
    H->>H: edits the draft locally
    H->>C: decide.override<br/>(diff + rationale + tags)
    C->>C: produce override artefact<br/>+ extend evidence chain
    C->>A: notify.message<br/>(override captured)
    Note over C: override artefact links<br/>back to original draft via<br/>"based_on" field
```

**Why this matters.** Without CHAP, an override is "the human changed
something and clicked Save." With CHAP, it is a typed, signed, tagged,
diff-bearing artefact that downstream systems can learn from without
reverse-engineering the UI. Override patterns are now an analysable
asset of the workspace, not a tribal-knowledge loss.

---

## 7. Multi-human deliberation

When more than one human needs to weigh in, CHAP carries the thread.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "22px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#1a1a1c",
    "actorBkg": "#EA4700",
    "actorTextColor": "#ffffff",
    "actorBorder": "#C73D00"
  }
}}%%
sequenceDiagram
    autonumber
    participant C as Coordinator
    participant H1 as Human (Eng Lead)
    participant H2 as Human (Security)
    participant H3 as Human (Product)

    C->>H1: deliberate.open (decision: ship hotfix?)
    C->>H2: deliberate.open
    C->>H3: deliberate.open
    H1->>C: deliberate.comment ("risk seems low")
    H2->>C: deliberate.comment ("CVE-2026-1234 still open")
    H3->>C: deliberate.comment ("CSAT impact significant")
    H1->>C: deliberate.vote (yea, weight 1)
    H3->>C: deliberate.vote (yea, weight 1)
    H2->>C: deliberate.vote (nay, veto)
    C->>C: rule: weighted_vote_with_veto<br/>→ outcome: rejected
    C->>H1: deliberate.close (outcome + reasoning)
    C->>H2: deliberate.close
    C->>H3: deliberate.close
```

**Decision rules** are workspace policy. The protocol supports
`any_one_approves`, `all_approve`, `quorum:n`, `weighted_vote:threshold`,
and `weighted_vote_with_veto:threshold` out of the box.

---

## 8. Composition: CHAP + MCP + A2A

A real deployment composes all three protocols. Here is the full picture.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "20px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#4f4f52"
  },
  "flowchart": { "curve": "linear", "nodeSpacing": 50, "rankSpacing": 60, "padding": 16 }
}}%%
flowchart TB
    classDef human fill:#FFF8F2,stroke:#1a1a1c,stroke-width:2px,color:#1a1a1c
    classDef agent fill:#FFE7DD,stroke:#EA4700,stroke-width:2px,color:#5a1500
    classDef coord fill:#EA4700,stroke:#C73D00,stroke-width:2.5px,color:#ffffff
    classDef tool  fill:#E8F1ED,stroke:#1f5b39,stroke-width:2px,color:#0a3a1c
    classDef bridge fill:#EDE0F2,stroke:#6a3d8a,stroke-width:2px,color:#3b0e63
    classDef ext   fill:#FFF3E0,stroke:#C76B00,stroke-width:2px,color:#5a3500

    subgraph WS["<b>CHAP Workspace</b>"]
      H["Human"]:::human
      A["Agent"]:::agent
      C["<b>Coordinator</b>"]:::coord
      B["A2A Bridge"]:::bridge
    end

    T1["Tool<br/>(orders)"]:::tool
    T2["Tool<br/>(shipping)"]:::tool
    EXT["External Agent<br/>(partner org)"]:::ext

    H -->|CHAP| C
    A -->|CHAP| C
    B -->|CHAP| C
    A -.->|MCP| T1
    A -.->|MCP| T2
    B ====>|A2A| EXT
```

**The audit story.** A regulator asks "show me everything that produced
this customer reply." The Coordinator returns:

- The CHAP messages (signed, hash-linked).
- The cited MCP tool invocations (with hash-verified inputs and outputs).
- The cited A2A correlations (with cross-system attestation).

One query, one chain, three protocols.

---

## 9. Deployment topologies

CHAP supports three deployment topologies, each with different trust and
operational trade-offs.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "20px",
    "fontFamily": "Arial, Helvetica, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#1a1a1c",
    "primaryBorderColor": "#1a1a1c",
    "lineColor": "#4f4f52",
    "clusterBkg": "#FFF8F2",
    "clusterBorder": "#1a1a1c"
  },
  "flowchart": { "curve": "linear", "nodeSpacing": 50, "rankSpacing": 60, "padding": 18 }
}}%%
flowchart TB
    classDef coord fill:#EA4700,stroke:#C73D00,stroke-width:2.5px,color:#ffffff
    classDef part  fill:#FFF8F2,stroke:#1a1a1c,stroke-width:2px,color:#1a1a1c
    classDef peer  fill:#EDE0F2,stroke:#6a3d8a,stroke-width:2px,color:#3b0e63

    subgraph T1["<b>1. Coordinator-mediated</b>"]
      direction LR
      P1A["participant"]:::part
      P1B["participant"]:::part
      P1C["participant"]:::part
      C1["<b>Coordinator</b>"]:::coord
      P1A --- C1
      P1B --- C1
      P1C --- C1
    end

    subgraph T2["<b>2. Peer-to-peer</b>"]
      direction LR
      P2A["participant"]:::part
      P2B["participant"]:::part
      P2C["participant"]:::part
      P2A --- P2B
      P2B --- P2C
      P2A --- P2C
    end

    subgraph T3["<b>3. Federated</b>"]
      direction LR
      C3A["<b>Coord A</b>"]:::coord
      BR["<b>Bridge</b><br/>A2A"]:::peer
      C3B["<b>Coord B</b>"]:::coord
      C3A --- BR
      BR --- C3B
    end

    T1 ~~~ T2
    T2 ~~~ T3
```

**1. Coordinator-mediated.** The default. One Coordinator per workspace,
responsible for routing, policy enforcement, and the evidence chain.
Simple, easy to operate, single point of failure (mitigated by
Coordinator HA).

**2. Peer-to-peer.** No central Coordinator; participants gossip
messages and each maintains a local copy of the chain. Suited to small,
high-trust workspaces. Requires CRDT-style convergence for the chain
head; defined as an extension for v0.2.

**3. Federated.** Each organisation runs its own Coordinator; cross-org
work moves over A2A via a bridge participant. The local chain remains
authoritative within each organisation; cross-org evidence joins via
the bridge's citations.

---

## 10. Performance characteristics

A reference Coordinator on a single 8-core machine handles, by
measurement on the reference implementation:

| Operation                          | Throughput          | p99 latency |
|------------------------------------|---------------------|-------------|
| Envelope verification (Ed25519+JCS)| ~12,000 msg/sec     | 1.8 ms      |
| Evidence append (no fsync)         | ~25,000 entries/sec | 0.4 ms      |
| Evidence append (fsync per entry)  | ~3,000 entries/sec  | 4.5 ms      |
| Chain replay (verify)              | ~40,000 entries/sec | n/a         |
| Full `audit.verify` over 100k entries | n/a              | 2.6 s       |

These numbers are indicative, not normative. Real throughput depends on
payload size, signature cache hit rate, and storage backend. The
chain-append operation is intentionally cheap; the signature
verification is the dominant cost.

---

## 11. What CHAP is not

To avoid scope creep:

- **Not a workflow engine.** CHAP carries the messages a workflow
  engine produces. The state of *which task comes next* lives in the
  application, not the protocol.
- **Not a knowledge base.** Artefacts are typed payloads; their
  semantics are application-defined.
- **Not a chat protocol.** `notify.message` exists but is intended
  for protocol-adjacent communication, not as a Slack replacement.
- **Not a permission system.** Roles and method-permission matrices
  live in the workspace policy; CHAP carries the policy reference and
  enforces it.
- **Not an identity provider.** CHAP relies on OIDC, SPIFFE, and
  workload identities; it does not issue tokens.

---

## 12. Open questions for the next draft

These items are tracked in [CHANGELOG.md](./CHANGELOG.md) and
[CONTRIBUTING.md](./CONTRIBUTING.md):

1. **Confidentiality extension.** Per-field encryption for evidence
   entries with sensitive content.
2. **Peer-to-peer chain convergence.** CRDT-style chain merging for
   the peer-to-peer topology.
3. **Cross-workspace evidence joins.** A canonical algorithm for
   joining chains across federated deployments.
4. **Capability descriptor.** A finer-grained alternative to the three
   conformance levels.
5. **Post-quantum signatures.** Hybrid Ed25519 + ML-DSA option.
6. **Interop test suite.** A formal conformance harness with negative
   tests.
