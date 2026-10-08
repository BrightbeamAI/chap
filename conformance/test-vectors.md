# CHAP Conformance Test Vectors

This document provides canonical input/output pairs that an
implementation can use to self-check its signing, canonicalisation,
and evidence-chain code. The values are reproducible: anyone with a
working Ed25519 and SHA-256 library can regenerate them.

The vectors cover:

1. **Ed25519 signing** (against RFC 8032 test vector 1).
2. **JCS canonicalisation** of a sample CHAP envelope.
3. **Review and audit behaviour**: the harness vectors `rv-09` to `rv-13`
   (§2a, §2c) and the `audit-scitt/1.0` vectors `av-01` to `av-05` (§2b).
4. **Evidence-chain linkage** of a chain both coordinators record (§3).

If an implementation matches the signing, canonicalisation and chain
vectors, its cryptographic core is conformant. (The conformance ladder,
Minimal, Recommended and the planned Full level, is described in
[SPECIFICATION.md §17](../SPECIFICATION.md#17-conformance) and the
profile-selection checklist is in
[`conformance-checklist.md`](./conformance-checklist.md).)

---

## 1. Ed25519 signing (RFC 8032 test vector 1)

This is the canonical Ed25519 test vector. Any correct
implementation MUST produce the listed signature.

```
SEED (private key seed, 32 bytes, hex):
  9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60

EXPECTED PUBLIC KEY (32 bytes, hex):
  d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a

MESSAGE TO SIGN: empty (0 bytes)

EXPECTED SIGNATURE (64 bytes, hex):
  e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b
```

In CHAP, the signature is base64-encoded and prefixed with the
algorithm and key id, e.g.

```
ed25519:test-vector-rfc8032:5VZDAMNgrHKQhuLMgG6CioSHfx645dl02HPgZSJJAVVfuIIVkKM7rMYeOXAc+bRr0lv18FlbviRlUUFDjnoQCw==
```

If your `ed25519:` tag's base64 decodes to the 64-byte signature
above, you're conformant on this vector.

---

## 2. JCS canonicalisation

JCS (RFC 8785) is required because Ed25519 signing is over bytes,
and signing arbitrary JSON requires a deterministic byte
representation. The CHAP rules:

- Keys sorted lexicographically at every nesting level.
- No insignificant whitespace.
- UTF-8 encoding.
- Strings use minimal JSON escaping.
- Numbers are integers within the safe-integer range. A non-integer
  number is rejected rather than canonicalised, so a fractional value
  travels as a decimal string. See SPECIFICATION.md §5.2 and the
  machine-readable cases in `canonical-number-vectors.json`.
- The `evidence.sig` field is **removed** before canonicalisation
  for signing (and reinserted after).

### Sample envelope

```json
{
  "chap": "0.2",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
  "ts": "2026-05-17T09:00:00.000Z",
  "workspace": "wsp_test",
  "from": "human:alice@example.org",
  "to": "service:coordinator@example.org",
  "type": "notification",
  "method": "participant.heartbeat",
  "params": { "load": "0.42", "status": "ready" },
  "evidence": { "prev_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000" }
}
```

### Expected canonical form (exact bytes)

```
{"chap":"0.2","evidence":{"prev_hash":"sha256:0000000000000000000000000000000000000000000000000000000000000000"},"from":"human:alice@example.org","id":"01HZ9YWQ7K3X8M2V4N6P8R0T2A","method":"participant.heartbeat","params":{"load":"0.42","status":"ready"},"to":"service:coordinator@example.org","ts":"2026-05-17T09:00:00.000Z","type":"notification","workspace":"wsp_test"}
```

### Expected SHA-256 of the canonical bytes

```
sha256:d07ac6c9ca7a88ac8578b342ac9845875750e298b613f1cef768bece1a8faf51
```

If your JCS implementation produces those exact bytes and that
exact hash, you're conformant on canonicalisation. If the hash
differs but the bytes look almost right, check (in order):

- Key ordering at every nesting level (especially `evidence` and `params`).
- Whitespace: there must be none.
- Number representation: `load` is the string `"0.42"`, copied byte for byte.
  A JSON number `0.42` has no CHAP canonical form, and a Coordinator refuses a
  call that carries one with `-32602`.
- The presence of `evidence.sig`: it must be **absent** during canonicalisation.

---

## 2a. Refusals that must leave state untouched

Four vectors cover the artefact digest, the open-review guard and the reviewer
set a required review is addressed to. `rv-09` checks that a matching digest
approves as normal. The others assert on a refusal, which is easy to implement
as a refusal that quietly does not.

| Vector  | Sends                                                        | Expects                                   |
|---------|--------------------------------------------------------------|-------------------------------------------|
| `rv-09` | `decide.approve` with `approved_artefact_digest` equal to `sha256:` + SHA-256 over the JCS form of the artefact under review | approval proceeds, task `completed`       |
| `rv-10` | the same with a digest of different content                   | `-32074`, no decision takes effect, and the review still open so the reviewer can decide afterwards |
| `rv-11` | `review.request` on an open review carrying different content  | `-32014`; then the identical artefact returns `amended: true` |
| `rv-12` | `task.complete` on a `review_required` task, then `decide.approve` from a second agent in the workspace | the completion opens a review; the agent's decision is refused with `-32011`; a human the review was addressed to completes it |

`rv-10` deliberately decides again after the refusal. A Coordinator that let
the refused decision take effect, or that closed the review, fails on the
second call rather than the first.

---

## 2b. Verification coverage (`audit-scitt/1.0`)

Five vectors covering what `audit.verify_chain` may and may not call a pass.
Each asserts on the verdict a Coordinator gives about a range it did not
evaluate, which is easy to implement as a pass with a smaller count beside
it.

| Vector  | Sends                                                        | Expects                                   |
|---------|--------------------------------------------------------------|-------------------------------------------|
| `av-01` | `audit.verify_chain` on a workspace chained from creation     | `status: "verified"`, `ok: true`, `entries_unchecked: 0` |
| `av-02` | the same after `workspace.set_profiles` added `audit-scitt/1.0` to a workspace with existing entries | `status: "not_evaluated"`, `ok: false`, `reason: "unchained_prefix"`, `entries_unchecked` equal to the entries written before the enabling call |
| `av-03` | the same on a workspace that never enabled chaining           | `-32602`; a refusal, not a verdict        |
| `av-04` | the same after tampering with a covered entry                 | an error; tampering is never downgraded to `not_evaluated` |
| `av-05` | `audit.verify_chain` with `from_seq` or `to_seq`              | `-32602`; a narrowing range is refused, never widened to the whole log |

`av-02` is the vector that matters. `ok` must be `false` even though every
covered entry replayed cleanly, because the question asked was about the
whole log. `entries_checked` plus `entries_unchecked` must equal
`entries_total` in every verdict, and `ok` must be `true` only when `status`
is `verified`.

The harness runs Core and `review/1.0` against reference servers that do not
enable `audit-scitt/1.0`, so these five are covered by unit tests in both
reference implementations instead: `av-01`,
`av-02`, `av-04` and `av-05` in `verify_coverage.test.ts` and
`test_verify_coverage.py`, and `av-03` in `verify_chain_unchained.test.ts`
and `test_verify_chain_unchained.py`. The two implementations answer them
identically.

---

## 2c. Refused calls on the log

SPECIFICATION §10.1 has a Coordinator record a member's refused call under
`request`, with an `outcome`, and leave a non-member's off the log.
`refusal-record-vectors.json` pins the recorded entries and chain heads for
the two coordinator packages. Over HTTP, one harness vector reads back the
refusals of `rv-07` and `rv-08`:

| Vector  | Sends                                                        | Expects                                   |
|---------|--------------------------------------------------------------|-------------------------------------------|
| `rv-13` | `audit.read` with `filter: {task_id, outcome: "refused"}` for the task of `rv-07` and `rv-08` | one entry: the bystander's `decide.approve` under `request`, no `envelope`, and `outcome` `{"status": "refused", "code": -32011}`. The non-member's attempt is absent |

---

## 3. Evidence-chain linkage

The chain is a sequence of entries. Each entry carries the chain head
as it stood before that entry, and the new head is the SHA-256 of the
entry's canonical record concatenated with that previous head.

```
entry[N].prev_hash = head before entry N   (sha256:0*64 for the first entry)
head after entry N = sha256( JCS(record[N]) || entry[N].prev_hash )
```

For an accepted call, `record[N]` is the envelope exactly as received, `id`
and any top-level `sig` included. For a recorded refusal (SPECIFICATION
§10.1), it is the object `{"outcome": …, "request": …}`.
`refusal-record-vectors.json` pins worked examples of those. The entry's own
`seq` and `arrived` are not part of the record.

Every digest is `sha256:` followed by 64 lowercase hex characters.
`JCS(record[N])` is the canonical serialisation of the record; `prev_hash`
is concatenated as its full UTF-8 string form, prefix included. The first
chained entry, which SPECIFICATION §10.1 calls the genesis entry, carries
`sha256:` followed by 64 zeros as its `prev_hash`.

Below is the chain both coordinators record for three accepted calls on a
workspace created with `audit-scitt/1.0`: the `workspace.create` that
created it and two `participant.join` calls. Each record is shown in its
canonical form.

### Entry 0 (seq = 0)

```
record    = {"id":"req-1","jsonrpc":"2.0","method":"workspace.create","params":{"from":"human:alice@example.org","profiles":["core/1.0","audit-scitt/1.0"],"to":"service:coordinator@example.org","ts":"2026-05-17T09:00:00.000Z","workspace":"wsp_test"}}
prev_hash = sha256:0000000000000000000000000000000000000000000000000000000000000000

→ head after entry 0:
  sha256:110153f2ff7df935174bab8ddfe48a303920bec27deca0e55647cdacf3c0ea97
```

### Entry 1 (seq = 1)

```
record    = {"id":"req-2","jsonrpc":"2.0","method":"participant.join","params":{"from":"human:alice@example.org","to":"service:coordinator@example.org","ts":"2026-05-17T09:00:01.000Z","type":"human","workspace":"wsp_test"}}
prev_hash = sha256:110153f2ff7df935174bab8ddfe48a303920bec27deca0e55647cdacf3c0ea97

→ head after entry 1:
  sha256:77e0e38d2006c83645a1c8cb12abb401c4e109320fcf7b94a94753113e0cb19d
```

### Entry 2 (seq = 2)

```
record    = {"id":"req-3","jsonrpc":"2.0","method":"participant.join","params":{"from":"agent:triage-bot","to":"service:coordinator@example.org","ts":"2026-05-17T09:00:02.000Z","type":"agent","workspace":"wsp_test"}}
prev_hash = sha256:77e0e38d2006c83645a1c8cb12abb401c4e109320fcf7b94a94753113e0cb19d

→ head after entry 2 (the chain head, published as evidence_head):
  sha256:5d9e95b79b484be957f462d23ea61febcfa124146f3acae2e194878c0a750699
```

### Verification recipe

Check that the first chained entry's `prev_hash` is the zero head. Then,
for each i ≥ 1:

```
expected_prev_hash_at_i = sha256( JCS(record[i-1]) + entry[i-1].prev_hash )
assert entry[i].prev_hash == expected_prev_hash_at_i
```

Finally, the head after the last entry must equal the workspace's
`evidence_head`. If your chain walker reports the values above, you've
verified linkage.

---

## 4. Reproducing these vectors

A short Python script that regenerates §3:

```python
import hashlib

def h(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode()).hexdigest()

# The canonical (JCS) form of each recorded envelope, as in section 3.
records = [
    '{"id":"req-1","jsonrpc":"2.0","method":"workspace.create","params":{"from":"human:alice@example.org","profiles":["core/1.0","audit-scitt/1.0"],"to":"service:coordinator@example.org","ts":"2026-05-17T09:00:00.000Z","workspace":"wsp_test"}}',
    '{"id":"req-2","jsonrpc":"2.0","method":"participant.join","params":{"from":"human:alice@example.org","to":"service:coordinator@example.org","ts":"2026-05-17T09:00:01.000Z","type":"human","workspace":"wsp_test"}}',
    '{"id":"req-3","jsonrpc":"2.0","method":"participant.join","params":{"from":"agent:triage-bot","to":"service:coordinator@example.org","ts":"2026-05-17T09:00:02.000Z","type":"agent","workspace":"wsp_test"}}',
]

prev = "sha256:" + "0" * 64  # the zero head
for seq, record in enumerate(records):
    head = h(record + prev)  # JCS(record) || prev_hash, as UTF-8
    print(f"seq {seq}: prev_hash {prev}")
    print(f"       head      {head}")
    prev = head
```

Run it; the printed heads must match §3. If they do, your hashing and
linkage rules are correct.

---

## 5. What these vectors do NOT cover

These vectors check the cryptographic and structural primitives.
They do not (and cannot) check:

- That an implementation enforces method-role authorisation.
- That an implementation handles step-up authentication correctly.
- That a Coordinator behaves correctly under concurrent writes.
- That mode-ceiling and policy enforcement work end-to-end.

Those behaviours are tested by integration tests against a running
deployment. See [`conformance-checklist.md`](./conformance-checklist.md)
for the self-attestation template that covers them.
