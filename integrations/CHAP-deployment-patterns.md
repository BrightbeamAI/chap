# CHAP Deployment Patterns

This document describes how to deploy CHAP in production. It is
vendor-neutral; it does not assume a specific Coordinator implementation,
storage backend, or cloud provider. The patterns here come from common
multi-agent system topologies and have been distilled into three
reference deployments.

For the wire format and method catalogue, see [`SPECIFICATION.md`](../SPECIFICATION.md).
For the architectural model, see [`ARCHITECTURE.md`](../ARCHITECTURE.md).

---

## 1. The deployment decision space

Four orthogonal choices shape a CHAP deployment:

| Axis              | Options                                                       |
|-------------------|---------------------------------------------------------------|
| **Topology**      | coordinator-mediated · peer-to-peer · federated               |
| **Tenancy**       | single-tenant · multi-tenant SaaS · on-premises               |
| **Transport**     | WebSocket · HTTP + SSE · message broker (NATS / Kafka / etc.) |
| **Storage**       | append-only log · transactional DB · object store (cold)      |

Each combination is workable. The three reference deployments at
§6 cover the most common pairings.

---

## 2. Topologies

### 2.1 Coordinator-mediated (recommended default)

```
┌──────────────────────────────────────────────────────┐
│              Workspace W                             │
│                                                      │
│   human ──┐                  ┌── agent               │
│           ├──> Coordinator <─┤                       │
│   human ──┘   (mediates,      └── agent              │
│                 signs ack,                           │
│                 keeps chain)                         │
└──────────────────────────────────────────────────────┘
```

The Coordinator is a logical role; in production it is typically a
clustered service. Every message flows through the Coordinator, which:

- Verifies signatures.
- Enforces workspace policy (member list, mode ceiling, method-role matrix).
- Appends to the evidence chain.
- Fans messages out to recipients.
- Optionally co-signs to attest acceptance.

This topology is the easiest to operate and the most common in
production. The Coordinator is a critical-path component — see §3 on
high availability.

### 2.2 Peer-to-peer (advanced)

```
┌────────────────────────────┐
│        Workspace W         │
│                            │
│   human <──────> agent     │
│      ▲             ▲       │
│      └──── peer ───┘       │
│        (gossip chain)      │
└────────────────────────────┘
```

Participants exchange messages directly, with a gossip-style protocol
maintaining the evidence chain across peers. There is no single
Coordinator; each participant is its own Coordinator for the messages
it originates and receives.

Trade-offs:

- No single point of failure.
- Eventual consistency on chain ordering — peers may briefly disagree
  on the head before gossip converges.
- Policy enforcement becomes a distributed problem.

This topology is appropriate for high-trust environments or
specialised deployments. Most teams should pick coordinator-mediated.

### 2.3 Federated

```
┌──── Workspace A ────┐         ┌──── Workspace B ────┐
│                     │         │                     │
│   human ─┐          │         │          ┌─ human   │
│          ├> Coord-A │ <─A2A─> │ Coord-B <┤          │
│   agent ─┘          │         │          └─ agent   │
│                     │         │                     │
└─────────────────────┘         └─────────────────────┘
```

Each workspace has its own Coordinator and chain. Cross-workspace
work crosses an A2A bridge (see [`CHAP-with-A2A.md`](./CHAP-with-A2A.md)).
Each chain remains authoritative locally; cross-chain evidence is
joined by citation.

This is the right topology for cross-organisational collaboration.

---

## 3. High-availability Coordinator

In coordinator-mediated topologies the Coordinator is on the
critical path for every message, so it needs to be HA.

A typical configuration:

```
                ┌────────────┐
                │  Load       │
   clients ──→  │  balancer   │  ──→ N × Coordinator instances
                │  (sticky    │              │
                │   by ws id) │              ▼
                └────────────┘    ┌────────────────────────┐
                                  │ Append-only chain log  │
                                  │ (Postgres, Kafka, etc.)│
                                  └────────────────────────┘
                                              ▲
                                              │
                                      ┌───────────────┐
                                      │ Leader-elect  │
                                      │ (etcd, Raft)  │
                                      └───────────────┘
```

Required properties:

- **One writer per workspace at any moment.** The chain is
  append-only and order-sensitive; concurrent writers cause
  `prev_hash` races. Achieve this with leader election keyed on
  workspace id (Raft, etcd lease, Redis Redlock, or your platform's
  equivalent).
- **Sticky routing of clients.** A client should reach the same
  Coordinator instance for the same workspace until that instance
  fails over. This minimises cross-instance state sync.
- **Standby readers.** Non-leader instances can serve `workspace.describe`,
  `audit.read`, and other read-only methods.
- **Failover budget.** A workspace becomes write-unavailable during
  leader election. Typical recovery: 1–5 seconds with etcd / Raft
  leases.

---

## 4. Storage backends

CHAP's evidence chain is append-only and the read patterns are
predictable (forward scan from a known seq; tail-follow for live
subscribers). Storage choices, ranked:

### 4.1 Append-only log (Kafka, Pulsar, NATS JetStream)

Best fit for the chain itself. The log's natural ordering matches the
chain's ordering. Compaction is forbidden for the audit data
(append-only is a hard requirement); tiered storage (hot → cold) is
fine.

Common pattern: one topic per workspace, or one topic partitioned by
workspace id with sticky partitioning.

### 4.2 Transactional database (Postgres, MySQL)

Works well for moderate throughput. Schema sketch:

```sql
CREATE TABLE evidence (
  workspace_id   TEXT      NOT NULL,
  seq            BIGINT    NOT NULL,
  prev_hash      TEXT      NOT NULL,
  envelope_hash  TEXT      NOT NULL,
  from_uri       TEXT      NOT NULL,
  ts             TIMESTAMPTZ NOT NULL,
  method_or_type TEXT      NOT NULL,
  envelope_id    TEXT,
  envelope_jsonb JSONB     NOT NULL,
  sig            TEXT      NOT NULL,
  coord_sig      TEXT,
  PRIMARY KEY (workspace_id, seq)
);

CREATE INDEX evidence_by_envelope_id ON evidence (envelope_id);
CREATE INDEX evidence_by_ts          ON evidence (workspace_id, ts);
```

A row-level append guard:

```sql
INSERT INTO evidence (...)
SELECT ...
WHERE NOT EXISTS (
  SELECT 1 FROM evidence
  WHERE workspace_id = $1 AND seq = $2
);
```

…in conjunction with the leader-election ensures atomic append.

### 4.3 Object store for cold archive

After a chain segment passes its retention threshold for hot
storage, archive to an object store (S3, GCS) as a signed Parquet
or JSON-Lines file. The `audit.export` method produces such files.

Retain the chain head pointer + recent N entries in hot storage;
fetch from cold storage on demand for older audit queries.

---

## 5. Transports

| Transport         | Latency  | Throughput | Best for                                  |
|-------------------|----------|------------|-------------------------------------------|
| WebSocket         | Low      | Medium     | Interactive humans, agents over the public internet. |
| HTTP + SSE        | Low–medium | Medium   | Firewall-friendly fallback for WebSocket. |
| HTTP poll         | High     | Low        | Restricted-network agents.                |
| NATS / JetStream  | Low      | High       | Intra-mesh agent meshes.                  |
| Kafka             | Medium   | Very high  | High-volume server-to-server flows.       |
| RabbitMQ / SQS    | Medium   | High       | Reliable queues; offline-tolerant agents. |

Multiple transports can coexist; the wire format is identical. The
Coordinator's transport adaptors normalise to the same internal
envelope.

---

## 6. Reference deployments

### 6.1 Single-tenant on a VM pair

Use case: one team, one workspace, on-premises or a single cloud account.

```
┌────────────────────────────────────────────────────────┐
│  vm-1 (primary)                                        │
│  ├─ Coordinator (leader)                               │
│  ├─ Postgres (primary)                                 │
│  └─ Object store mount (read-write)                    │
├────────────────────────────────────────────────────────┤
│  vm-2 (standby)                                        │
│  ├─ Coordinator (read-only, ready to fail over)        │
│  └─ Postgres (replica, hot standby)                    │
└────────────────────────────────────────────────────────┘
```

- Transport: WebSocket.
- Storage: Postgres for hot, object store for cold.
- Identity: OIDC against the org's IdP; service accounts via OAuth 2.0 client credentials.
- Backup: daily Postgres snapshot + continuous WAL shipping; weekly cold-archive export to object store.
- DR target: RPO ≤ 1 minute, RTO ≤ 5 minutes.

### 6.2 Multi-tenant SaaS

Use case: a service provider hosting CHAP workspaces for many customer organisations.

```
┌────────────────────────────────────────────────────────┐
│  Region (one of several)                               │
│                                                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Coord-A  │  │ Coord-B  │  │ Coord-C  │  (autoscaled)│
│  └────┬─────┘  └────┬─────┘  └────┬─────┘              │
│       └────────────┬┴─────────────┘                    │
│                    ▼                                   │
│           ┌─────────────────┐                          │
│           │ etcd / leader   │                          │
│           │ election plane  │                          │
│           └─────────────────┘                          │
│                    │                                   │
│                    ▼                                   │
│      ┌──────────────────────────┐                      │
│      │ Kafka cluster            │                      │
│      │ (one topic per workspace)│                      │
│      └──────────────────────────┘                      │
│                    │                                   │
│                    ▼                                   │
│      ┌──────────────────────────┐                      │
│      │ Cold archive (S3)        │                      │
│      └──────────────────────────┘                      │
└────────────────────────────────────────────────────────┘
```

- Transport: WebSocket for interactive clients, HTTP + SSE fallback, Kafka direct for high-volume agent fleets.
- Storage: Kafka for hot chain, S3 for cold.
- Tenancy isolation: per-workspace topic in Kafka; per-tenant IAM on S3 prefixes.
- Per-tenant key material: each tenant's OIDC trust is configured separately; Coordinators are otherwise multi-tenant.
- Compliance: per-region deployments for data residency; cross-region replication is opt-in per workspace.

### 6.3 On-prem regulated deployment

Use case: a regulated organisation (financial services, healthcare) running CHAP entirely inside its data centre.

```
┌────────────────────────────────────────────────────────────┐
│  Data centre                                                │
│                                                             │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐         │
│  │ Coord-1    │    │ Coord-2    │    │ Coord-3    │         │
│  └─────┬──────┘    └─────┬──────┘    └─────┬──────┘         │
│        └─────────────────┴──────────────────┘               │
│                          │                                  │
│                ┌─────────▼──────────┐                       │
│                │ Postgres cluster   │                       │
│                │ + WAL archive      │                       │
│                └────────────────────┘                       │
│                          │                                  │
│                ┌─────────▼──────────┐                       │
│                │ Tape / WORM        │                       │
│                │ for cold audit     │                       │
│                └────────────────────┘                       │
│                                                             │
│  Identity: internal OIDC IdP + SPIFFE service mesh          │
│  Network: private VLAN, mTLS everywhere                     │
│  Audit anchoring: per-day checkpoint hash signed by         │
│    workspace admin and stored offline (WORM media)          │
└─────────────────────────────────────────────────────────────┘
```

- Transport: WebSocket on the internal VLAN; mTLS terminated at the load balancer.
- Storage: Postgres + WAL + WORM cold tier.
- Identity: SPIFFE for services, internal OIDC for humans.
- Compliance: daily checkpoint hash exported to immutable media; quarterly auditor read access via `audit.read` with a scoped account.

---

## 7. Monitoring

A production deployment should expose, at minimum:

| Metric                                  | Why                                              |
|-----------------------------------------|--------------------------------------------------|
| `chap_messages_total{method,workspace}`  | Overall throughput and per-method usage.         |
| `chap_message_latency_ms{method}`        | End-to-end latency budget.                       |
| `chap_chain_append_latency_ms`           | Storage-write latency; primary SLO.              |
| `chap_signature_verify_failures_total`   | Should be near zero in steady state.             |
| `chap_step_up_required_total`            | Helps tune the step-up window.                   |
| `chap_abstain_total{category}`           | Workspace competence boundary signal.            |
| `chap_override_rate{agent}`              | Agent quality signal; spikes are alerts.         |
| `chap_whisper_lapse_total{workspace}`    | Coverage signal.                                 |
| `chap_evidence_count{workspace}`         | Chain growth; capacity planning input.           |
| `chap_coordinator_leader_election_total` | HA health.                                       |

Alerting rules worth having:

- Signature-verify failures > 0.1% sustained: misconfiguration or attack.
- Override rate spike vs. 7-day baseline (per agent): regression alarm.
- Step-up requirements declining sharply: policy regression — investigate.
- Chain-append latency p99 above SLO: storage problem.
- Whisper lapse rate above target: coverage gap.

---

## 8. Key management

Three classes of key, three management strategies:

| Class                | Lifetime         | Custody             | Rotation                         |
|----------------------|------------------|---------------------|----------------------------------|
| Human ephemeral      | One session      | Client device       | Each login.                      |
| Agent / service      | Days–weeks       | Workload identity   | Scheduled; `participant.rotate_key`. |
| Coordinator          | Months           | HSM or KMS          | Quarterly; admin operation.      |

Coordinator keys are the most sensitive (they co-sign acceptances).
HSM-backed or KMS-managed Coordinator keys are strongly recommended
for production deployments.

A revoked key is recorded in the chain (`participant.revoke_key`).
The Coordinator's verify path consults the key history when checking
signatures on historical entries: a key revoked at T must still verify
messages signed before T.

---

## 9. Disaster recovery

Recovery hinges on the chain. Two scenarios:

### 9.1 Coordinator failure

Stateful failover via leader election. The Coordinator's in-memory
state (open whispers, in-flight reviews) is reconstructed from the
chain on startup. RTO: seconds to a minute.

### 9.2 Storage corruption / total loss

- Hot storage: restore from continuous backup / cross-region replica.
- If hot storage is lost beyond recovery, fall back to cold archive.
- The chain is self-verifying: after restore, run `audit.verify` over
  the full range to confirm signatures and hash links survive.
- If a portion is permanently lost, write a sealed gap entry: a
  signed admin notification recording the lost range and the reason.
  Subsequent entries reference the post-gap state.

A chain with a sealed gap remains valid for the segments before and
after the gap; it is no longer auditable across the gap. Sealed gaps
should be rare and require admin attestation.

---

## 10. Operational runbook (skeleton)

A minimum operational runbook for a CHAP deployment covers:

| Procedure                                        | Reference                              |
|--------------------------------------------------|----------------------------------------|
| Onboard a new workspace                           | `workspace.create` + initial members.  |
| Onboard a new agent                              | Issue identity, configure scopes, run staged shadow → trial → production. |
| Rotate a Coordinator key                         | KMS rotation; broadcast new JWK.       |
| Pause / resume a misbehaving agent               | See [`examples/09-pause-resume-rollback.md`](../examples/09-pause-resume-rollback.md). |
| Investigate an audit incident                    | `audit.read` with filter on actor / time window. |
| Export an audit segment for compliance           | `audit.export`.                        |
| Apply a redaction (GDPR / right-to-be-forgotten) | `audit.redact` (preserves hash).       |
| Perform a chain-integrity check                  | `audit.verify` over the range.         |
| Restore from backup                              | See §9 above.                          |
| Handle a key-compromise incident                 | `participant.revoke_key` + chain audit.|

---

## 11. Recap

| Decision               | Default recommendation                                  |
|------------------------|---------------------------------------------------------|
| Topology               | Coordinator-mediated.                                   |
| HA                     | Leader election per workspace; sticky client routing.   |
| Storage (hot)          | Postgres for small/medium; Kafka for high-throughput.   |
| Storage (cold)         | Object store with signed exports.                       |
| Transport (interactive) | WebSocket; HTTP+SSE fallback.                          |
| Transport (server flows) | Kafka or NATS for high volume.                        |
| Identity (human)        | OIDC with `cnf.jwk` binding.                           |
| Identity (service)      | SPIFFE preferred; mTLS / OAuth client creds otherwise. |
| Coordinator key custody | HSM or KMS.                                            |

Start simple — a single coordinator pair with Postgres is sufficient
for most teams. Scale horizontally only when measured load demands it.
