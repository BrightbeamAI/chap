# HAP Architecture

This document is **informative**. It explains the design choices behind HAP,
how the primitives fit together, and what deployment topologies are practical.
For the normative wire format, see [SPECIFICATION.md](./SPECIFICATION.md).

---

## 1. The protocol stack

HAP sits alongside MCP and A2A. Each protocol owns a single concern.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "18px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111",
    "secondaryColor": "#f3f4f6",
    "tertiaryColor": "#fafafa"
  },
  "flowchart": { "curve": "linear", "nodeSpacing": 60, "rankSpacing": 80 }
}}%%
flowchart LR
    classDef human  fill:#dbeafe,stroke:#1e3a8a,stroke-width:2px,color:#0b1b3b
    classDef agent  fill:#fef3c7,stroke:#92400e,stroke-width:2px,color:#3b1d05
    classDef coord  fill:#fee2e2,stroke:#7f1d1d,stroke-width:2px,color:#3b0a0a
    classDef tool   fill:#dcfce7,stroke:#14532d,stroke-width:2px,color:#052e16
    classDef peer   fill:#e9d5ff,stroke:#581c87,stroke-width:2px,color:#1a0633

    H1["Human<br/>Reviewer"]:::human
    H2["Human<br/>Approver"]:::human
    A1["Agent<br/>Drafter"]:::agent
    C["Coordinator<br/>(HAP)"]:::coord
    T["Tool Server<br/>(MCP)"]:::tool
    P["Peer Agent<br/>(A2A)"]:::peer

    H1 ===|HAP| C
    H2 ===|HAP| C
    A1 ===|HAP| C
    A1 -.->|MCP| T
    A1 -.->|A2A| P
```

**Reading the diagram.** Solid lines are HAP. Dotted lines are MCP and A2A.
HAP sits in the middle, holding the workspace; MCP and A2A radiate outward
to tools and external agents respectively.

---

## 2. Core primitives

HAP has a small set of primitives, related as follows.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "17px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111"
  }
}}%%
classDiagram
    direction LR
    class Workspace {
      +id: wsp_xxx
      +state: active|paused|closed
      +mode: shadow|trial|production
      +members: Participant[]
      +policy_uri
      +evidence_head
    }
    class Participant {
      +uri
      +type: human|agent|service|group
      +jwks
      +capabilities
      +scopes
    }
    class Task {
      +id: tsk_xxx
      +kind
      +state
      +mode
      +assignee
      +delegator
      +artefacts
    }
    class Artefact {
      +id: art_xxx
      +kind
      +produced_by
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
      +id (ULID)
      +ts
      +from
      +to
      +type
      +method
      +evidence
    }

    Workspace "1" --> "*" Participant : members
    Workspace "1" --> "*" Task        : holds
    Workspace "1" --> "*" EvidenceEntry : append-only log
    Task      "1" --> "*" Artefact    : produces
    Message   "1" --> "1" EvidenceEntry : becomes
    Participant "1" --> "*" Message   : sends
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
    "fontSize": "16px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111"
  }
}}%%
stateDiagram-v2
    direction LR
    [*] --> Created
    Created --> Assigned: task.assign
    Assigned --> Accepted: task.accept
    Assigned --> Declined:  task.decline
    Accepted --> InProgress: task.start
    InProgress --> ReviewRequested: review.request
    InProgress --> Completed: task.complete (no review)
    ReviewRequested --> Completed: decide.approve / decide.override
    ReviewRequested --> InProgress: decide.reject (retry)
    InProgress --> Abstained: abstain.declare
    InProgress --> Escalated: escalate.raise
    InProgress --> Cancelled: control.cancel
    Accepted    --> Cancelled: control.cancel
    Assigned    --> Cancelled: control.cancel
    InProgress  --> Superseded: control.supersede
    Completed   --> [*]
    Declined    --> [*]
    Cancelled   --> [*]
    Abstained   --> [*]
    Escalated   --> [*]
    Superseded  --> [*]
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

HAP's audit guarantee is a per-workspace hash-linked log.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "16px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111"
  },
  "flowchart": { "curve": "linear", "nodeSpacing": 40, "rankSpacing": 30 }
}}%%
flowchart LR
    classDef entry fill:#f8fafc,stroke:#0f172a,stroke-width:2px,color:#0f172a
    classDef chkpt fill:#fef9c3,stroke:#854d0e,stroke-width:2px,color:#451a03
    classDef anchor fill:#dcfce7,stroke:#14532d,stroke-width:2px,color:#052e16

    E0["entry 0<br/>genesis"]:::entry
    E1["entry 1<br/>task.assign"]:::entry
    E2["entry 2<br/>task.accept"]:::entry
    E3["entry 3<br/>task.complete"]:::entry
    E4["entry 4<br/>review.request"]:::entry
    E5["entry 5<br/>decide.approve"]:::entry
    CK["checkpoint<br/>(every 1000)"]:::chkpt
    AN["external<br/>anchor"]:::anchor

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
    "fontSize": "16px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111"
  }
}}%%
flowchart TB
    classDef mode  fill:#ede9fe,stroke:#5b21b6,stroke-width:2px,color:#1e1338
    classDef ok    fill:#dcfce7,stroke:#14532d,stroke-width:2px,color:#052e16
    classDef block fill:#fee2e2,stroke:#7f1d1d,stroke-width:2px,color:#3b0a0a

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
    "fontSize": "16px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111",
    "actorBkg": "#f3f4f6",
    "actorTextColor": "#111111",
    "actorBorder": "#111111",
    "labelTextColor": "#111111",
    "noteBkgColor": "#fef9c3",
    "noteTextColor": "#451a03",
    "noteBorderColor": "#854d0e"
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

**Why this matters.** Without HAP, an override is "the human changed
something and clicked Save." With HAP, it is a typed, signed, tagged,
diff-bearing artefact that downstream systems can learn from without
reverse-engineering the UI. Override patterns are now an analysable
asset of the workspace, not a tribal-knowledge loss.

---

## 7. Multi-human deliberation

When more than one human needs to weigh in, HAP carries the thread.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "16px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111",
    "actorBkg": "#f3f4f6",
    "actorTextColor": "#111111",
    "actorBorder": "#111111"
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

## 8. Composition: HAP + MCP + A2A

A real deployment composes all three protocols. Here is the full picture.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "16px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111"
  }
}}%%
flowchart TB
    classDef human fill:#dbeafe,stroke:#1e3a8a,stroke-width:2px,color:#0b1b3b
    classDef agent fill:#fef3c7,stroke:#92400e,stroke-width:2px,color:#3b1d05
    classDef coord fill:#fee2e2,stroke:#7f1d1d,stroke-width:2px,color:#3b0a0a
    classDef tool  fill:#dcfce7,stroke:#14532d,stroke-width:2px,color:#052e16
    classDef bridge fill:#e9d5ff,stroke:#581c87,stroke-width:2px,color:#1a0633
    classDef ext   fill:#fce7f3,stroke:#9d174d,stroke-width:2px,color:#4a0726

    subgraph WS["HAP Workspace"]
      H["Human<br/>Reviewer"]:::human
      A["Agent<br/>Drafter"]:::agent
      C["Coordinator"]:::coord
      B["A2A Bridge<br/>(service)"]:::bridge
    end

    T1["Tool Server<br/>(orders)"]:::tool
    T2["Tool Server<br/>(shipping)"]:::tool
    EXT["External Agent<br/>(partner org)"]:::ext

    H -->|HAP| C
    A -->|HAP| C
    B -->|HAP| C
    A -.->|MCP| T1
    A -.->|MCP| T2
    B ====>|A2A| EXT

    Note1["The evidence chain inside the workspace<br/>cites the MCP calls and the A2A correlation IDs.<br/>One audit covers all three protocols."]
    C -. annotates .- Note1
```

**The audit story.** A regulator asks "show me everything that produced
this customer reply." The Coordinator returns:

- The HAP messages (signed, hash-linked).
- The cited MCP tool invocations (with hash-verified inputs and outputs).
- The cited A2A correlations (with cross-system attestation).

One query, one chain, three protocols.

---

## 9. Deployment topologies

HAP supports three deployment topologies, each with different trust and
operational trade-offs.

```mermaid
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontSize": "16px",
    "fontFamily": "Helvetica, Arial, sans-serif",
    "primaryColor": "#ffffff",
    "primaryTextColor": "#111111",
    "primaryBorderColor": "#111111",
    "lineColor": "#111111"
  }
}}%%
flowchart TB
    classDef coord fill:#fee2e2,stroke:#7f1d1d,stroke-width:2px,color:#3b0a0a
    classDef part  fill:#f3f4f6,stroke:#111111,stroke-width:2px,color:#111111
    classDef peer  fill:#e9d5ff,stroke:#581c87,stroke-width:2px,color:#1a0633

    subgraph T1["1. Coordinator-mediated (default)"]
      direction LR
      P1A["participant"]:::part
      P1B["participant"]:::part
      P1C["participant"]:::part
      C1["Coordinator"]:::coord
      P1A --- C1
      P1B --- C1
      P1C --- C1
    end

    subgraph T2["2. Peer-to-peer (small workspaces)"]
      direction LR
      P2A["participant<br/>+ local chain"]:::part
      P2B["participant<br/>+ local chain"]:::part
      P2C["participant<br/>+ local chain"]:::part
      P2A --- P2B
      P2B --- P2C
      P2A --- P2C
    end

    subgraph T3["3. Federated (cross-organisation)"]
      direction LR
      C3A["Coordinator A"]:::coord
      C3B["Coordinator B"]:::coord
      BR["Bridge<br/>(A2A)"]:::peer
      C3A --- BR
      BR --- C3B
    end
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

## 11. What HAP is not

To avoid scope creep:

- **Not a workflow engine.** HAP carries the messages a workflow
  engine produces. The state of *which task comes next* lives in the
  application, not the protocol.
- **Not a knowledge base.** Artefacts are typed payloads; their
  semantics are application-defined.
- **Not a chat protocol.** `notify.message` exists but is intended
  for protocol-adjacent communication, not as a Slack replacement.
- **Not a permission system.** Roles and method-permission matrices
  live in the workspace policy; HAP carries the policy reference and
  enforces it.
- **Not an identity provider.** HAP relies on OIDC, SPIFFE, and
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
