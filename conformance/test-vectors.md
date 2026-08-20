# CHAP Conformance Test Vectors

This document provides canonical input/output pairs that an
implementation can use to self-check its signing, canonicalisation,
and evidence-chain code. The values are reproducible: anyone with a
working Ed25519 and SHA-256 library can regenerate them.

The vectors cover three operations:

1. **Ed25519 signing** (against RFC 8032 test vector 1).
2. **JCS canonicalisation** of a sample CHAP envelope.
3. **Evidence-chain linkage** of a genesis entry plus three entries.

If an implementation matches all three, its cryptographic core is
conformant. (The conformance ladder. Minimal, Recommended, and the
planned Full level, is described in
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
  e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bdfa987599ce19a1c6d27
```

In CHAP, the signature is base64-encoded and prefixed with the
algorithm and key id, e.g.

```
ed25519:test-vector-rfc8032:5VZDAMNgrHKQhuLMgG6Cipw…
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
- Numbers in I-JSON-compatible form (integers if integer; otherwise
  shortest round-trip decimal).
- The `evidence.sig` field is **removed** before canonicalisation
  for signing (and reinserted after).

### Sample envelope

```json
{
  "chap": "0.2",
  "id": "01HZ9YWQ7K3X8M2V4N6P8R0T2A",
  "ts": "2026-05-17T09:00:00.000Z",
  "workspace": "wsp_test",
  "from": "human:[email protected]",
  "to": "service:[email protected]",
  "type": "notification",
  "method": "participant.heartbeat",
  "params": { "load": 0.42, "status": "ready" },
  "evidence": { "prev_hash": "sha256:0000000000000000000000000000000000000000000000000000000000000000" }
}
```

### Expected canonical form (exact bytes)

```
{"evidence":{"prev_hash":"sha256:0000000000000000000000000000000000000000000000000000000000000000"},"from":"human:[email protected]","chap": "0.2","id":"01HZ9YWQ7K3X8M2V4N6P8R0T2A","method":"participant.heartbeat","params":{"load":0.42,"status":"ready"},"to":"service:[email protected]","ts":"2026-05-17T09:00:00.000Z","type":"notification","workspace":"wsp_test"}
```

### Expected SHA-256 of the canonical bytes

```
sha256:3e2ca14b18d9c883ac22cc66c6b4b2a28012912212c5dd49b07b1fea8c1f52df
```

If your JCS implementation produces those exact bytes and that
exact hash, you're conformant on canonicalisation. If the hash
differs but the bytes look almost right, check (in order):

- Key ordering at every nesting level (especially `evidence` and `params`).
- Whitespace: there must be none.
- Number representation: `0.42` (not `0.420`, not `42e-2`).
- The presence of `evidence.sig`: it must be **absent** during canonicalisation.

---

## 2a. Binding a decision to its content (CEP-001)

Three vectors cover the artefact digest and the open-review guard. They are
listed here because they are the first vectors that assert on a *refusal* that
leaves state untouched, which is easy to implement as a refusal that quietly
does not.

| Vector  | Sends                                                        | Expects                                   |
|---------|--------------------------------------------------------------|-------------------------------------------|
| `rv-09` | `decide.approve` with `approved_artefact_digest` equal to `sha256:` + SHA-256 over the JCS form of the artefact under review | approval proceeds, task `completed`       |
| `rv-10` | the same with a digest of different content                   | `-32074`, no decision recorded, and the review still open so the reviewer can decide afterwards |
| `rv-11` | `review.request` on an open review carrying different content  | `-32014`; then the identical artefact returns `amended: true` |

`rv-10` deliberately decides again after the refusal. A Coordinator that
recorded the refused decision, or that closed the review, fails on the second
call rather than the first.

---

## 2b. Verification coverage (`audit-scitt/1.0`)

Four vectors covering what `audit.verify_chain` may and may not call a pass.
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

These five are not yet exercised by the harness, which runs Core and
`review/1.0` against reference servers that do not enable `audit-scitt/1.0`.
Both reference implementations are held to them by unit tests: `av-01`,
`av-02`, `av-04` and `av-05` in `verify_coverage.test.ts` and
`test_verify_coverage.py`, and `av-03` in `verify_chain_unchained.test.ts`
and `test_verify_chain_unchained.py`. The two implementations answer them
identically.

---

## 3. Evidence-chain linkage

The chain is a sequence of entries. Each entry carries the chain head
as it stood before that entry, and the new head is the SHA-256 of the
entry's canonical envelope concatenated with that previous head.

```
entry[N].prev_hash = head before entry N   (sha256:0*64 at genesis)
head after entry N = sha256( JCS(envelope[N]) || entry[N].prev_hash )
```

Every digest is `sha256:` followed by 64 lowercase hex characters.
`JCS(envelope[N])` is the canonical serialisation of the recorded
envelope; `prev_hash` is concatenated as its full UTF-8 string form,
prefix included. Genesis carries `sha256:` followed by 64 zeros.

Below is a four-entry chain (genesis plus three) with placeholder
canonical envelopes for entries 1-3. The hashes are derived
deterministically; any implementation that chains correctly will
produce the same `prev_hash` for each entry.

### Genesis (seq = 0)

```
envelope_hash = sha256:0000000000000000000000000000000000000000000000000000000000000000
sig           = ed25519:genesis:
                  (empty signature payload; the Coordinator MAY use
                   a zero-byte sig for genesis or a self-signature
                   over the genesis canonical form. The chain
                   linkage uses the literal string above.)

→ link hash (next entry's prev_hash):
  sha256:b648e6099b51884761cd73569c83a75bbf355d74e1b5d4ecab1ca264f99c1c9f
```

### Entry 1 (seq = 1)

```
canonical envelope (placeholder): {"e":"entry-1-canonical-form-placeholder"}
envelope_hash = sha256:c3c4a9b2fd30c2909f92e791dd087bef13a4a8741609f9050b8fff51bd2f3250
prev_hash     = sha256:b648e6099b51884761cd73569c83a75bbf355d74e1b5d4ecab1ca264f99c1c9f
sig           = ed25519:k-2026-05-17a:dGVzdHNpZzE=

→ link hash:
  sha256:f7bd68c5df49fadc8a33e2c9880df49f1199834a319fad39f824dc49c7ec31f7
```

### Entry 2 (seq = 2)

```
canonical envelope (placeholder): {"e":"entry-2-canonical-form-placeholder"}
envelope_hash = sha256:6041ae03445c4b444ea39f4582280bab47c4bc0b96441a1eaa275acacc6e7bb3
prev_hash     = sha256:f7bd68c5df49fadc8a33e2c9880df49f1199834a319fad39f824dc49c7ec31f7
sig           = ed25519:k-2026-05-17a:dGVzdHNpZzI=

→ link hash:
  sha256:47a6163f2d410223ea3463556bb9b97d09f0835b1f33556bc278f792d4306da4
```

### Entry 3 (seq = 3)

```
canonical envelope (placeholder): {"e":"entry-3-canonical-form-placeholder"}
envelope_hash = sha256:48af9d881f3fb72e18971ad5bef75ed427022615e57b7c65ec1d365e506a0d3b
prev_hash     = sha256:47a6163f2d410223ea3463556bb9b97d09f0835b1f33556bc278f792d4306da4
sig           = ed25519:k-2026-05-17a:dGVzdHNpZzM=

→ link hash (current chain head):
  sha256:2f232ae157206b416423e6dacb99925a1739a494072f05fe040788c874a82d05
```

### Verification recipe

For each i ≥ 1:

```
expected_prev_hash_at_i = sha256( JCS(envelope[i-1]) + entry[i-1].prev_hash )
assert entry[i].prev_hash == expected_prev_hash_at_i
```

If your chain walker reports those exact expected `prev_hash` values,
you've verified linkage. A real chain replaces the placeholder
envelopes with full CHAP envelopes; the linkage logic is the same.

---

## 4. Reproducing these vectors

A short Python script that regenerates §3:

```python
import hashlib

def h(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode()).hexdigest()

# Genesis
g_envelope_hash = "sha256:" + "0" * 64
g_sig           = "ed25519:genesis:"
g_link          = h(g_envelope_hash + g_sig)
print("genesis link:", g_link)

# Entry 1
e1_canonical    = '{"e":"entry-1-canonical-form-placeholder"}'
e1_envelope_hash = "sha256:" + hashlib.sha256(e1_canonical.encode()).hexdigest()
e1_sig           = "ed25519:k-2026-05-17a:dGVzdHNpZzE="
e1_link          = h(e1_envelope_hash + e1_sig)
print("entry 1 link:", e1_link)
# … repeat for entries 2 and 3.
```

Run this; the printed values must match the link hashes above. If
they do, your hashing and linkage rules are correct.

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
