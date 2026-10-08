# Profile: `audit-scitt`

**Profile id:** `audit-scitt/1.0` · **Depends on:** Core; pairs with `security-signed`.

The `audit-scitt` profile connects the workspace's audit log to a
[SCITT](https://datatracker.ietf.org/wg/scitt/about/) transparency
service. On request, the Coordinator turns each entry, an accepted CHAP
envelope or a recorded refusal (SPECIFICATION §10.1), into a SCITT
signed statement and passes it to a submitter the deployment supplies;
the receipts come back to the caller. Advertising the profile also
turns on the local hash chain (§6.1).

CHAP does not define its own transparency service; for that, this
profile defers to SCITT.

---

## 1. Why SCITT

SCITT is the IETF working group's standard for append-only,
cryptographically verifiable supply-chain statements. It is built
on COSE ([RFC 9052](https://datatracker.ietf.org/doc/html/rfc9052))
and produces receipts that:

- Anyone can verify with only the transparency service's public key
  and the receipt itself.
- Compose with existing supply-chain tooling (Sigstore Rekor,
  Notary v2, in-toto).
- Do not require a parallel verification implementation in CHAP.

Adopting SCITT means CHAP's audit story benefits from the IETF
working group's review and from existing SCITT implementations.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  CHAP Workspace                            │
│                                                           │
│   participants ── envelopes ──> Coordinator               │
│                                       │                   │
│                                       ▼                   │
│                            ┌──────────────────────┐       │
│                            │ Append to local log  │       │
│                            └──────────────────────┘       │
│                                       │                   │
│                                       ▼                   │
│                            ┌──────────────────────┐       │
│                            │  Submit to SCITT     │       │
│                            │  Transparency Service │       │
│                            └──────────────────────┘       │
│                                       │                   │
│                                       ▼                   │
│                            ┌──────────────────────┐       │
│                            │   SCITT receipt      │       │
│                            │   (returned to       │       │
│                            │    the caller)       │       │
│                            └──────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

The SCITT Transparency Service is its own component. It MAY be
operated by the same party as the Coordinator, by a third party
(notary), or by a federation of mutually-distrusting parties.

---

## 3. Statement format

Each entry is modelled as a SCITT signed statement. The Coordinator
returns this JSON model, or passes it to the deployment's submitter;
encoding it as COSE_Sign1 and signing it are the deployment's job.

```
COSE_Sign1 {
  protected: {
    alg: -8   // Ed25519
    iss: <issuer parameter; default "service:coordinator">
    kid: "scitt-issuer"
    cwt_claims: {
      sub: <workspace id>
      iat: null
    }
    content-type: "application/chap+json;version=0.2"
  }
  payload: <JCS canonicalisation of the entry's record>
  signature: "<deployment-supplied>"
}
```

The protected headers identify the workspace and the issuer. The
payload is the record the entry's chain link hashes
(SPECIFICATION §10.1). For an accepted call it is the canonical CHAP
envelope, which a receiver can extract and process normally. For a
recorded refusal it is the object `{"outcome": …, "request": …}`: the
call took no effect, and a receiver MUST NOT process its `request` as a
call.

---

## 3a. Methods

`audit.submit_to_scitt` takes an optional `range` {`from_seq`,
`to_seq`}, with `to_seq` exclusive and the whole log as the default,
and an optional `issuer`. With a submitter configured it returns
`{receipts: [{seq, receipt}]}`; without one it returns
`{statements, note}`. A failure stops it and discards earlier receipts:
`-32080` when the submitter raises an error, `-32081` when it returns
no receipt.

`audit.verify_receipt` returns `{verified: true}`, or `-32082` when the
deployment's verifier rejects the receipt or none is configured.

`audit.verify_chain` replays the local chain (§6.1). It refuses
`from_seq` or `to_seq`, a broken chain and a workspace without a chain
with `-32602`.

None of the three is recorded. Only `audit.submit_to_scitt` needs this
profile advertised; the other two answer whatever the workspace
advertises.

---

## 4. Receipt verification

A SCITT receipt is itself a COSE structure. To verify:

1. Validate the receipt's signature against the transparency
   service's published public key.
2. Confirm the receipt is for the statement you claim.
3. Confirm the receipt's inclusion proof links to a transparency
   log root the service has published.

Any third party can do this with only the receipt + the
transparency service's public key. The Coordinator is not required
in the loop.

---

## 5. What CHAP keeps

SCITT receipts add evidence from outside the workspace and leave CHAP's
own records in place. Advertising this profile turns on the `prev_hash`
chain (§6.1), which `audit.verify_chain` replays. `audit.checkpoint` and
`audit.verify` remain specified and unbuilt.

---

## 6. Importing pre-existing audit data

When adopting `audit-scitt` against an existing audit store (a
plain database log, a custom transparency log, or a different
append-only store), the recommended procedure:

1. For each historical entry in arrival order, construct a SCITT
   signed statement whose payload is the JCS canonicalisation of its
   record: the envelope, or for a recorded refusal the object
   `{"outcome": …, "request": …}`.
2. Preserve any pre-existing integrity metadata (chain hashes,
   coordinator signatures) inside the protected headers as
   informative fields.
3. Submit each statement to the SCITT transparency service.
4. Receipts for historical entries are issued retroactively; the
   resulting log is forward-verifiable from any historical point.

The historical entries remain auditable both via their original
provenance and via SCITT receipts. New entries reach SCITT when submitted.

### 6.1 Adopting the profile mid-life

Adding `audit-scitt/1.0` to a live workspace turns on the local
`prev_hash` chain from that point. Entries already in the log stay
outside it: no stored hash reaches back to them, so the chain says
nothing either way about whether they were altered.

`audit.verify_chain` reports this rather than passing over it: the verdict
for the whole log is `not_evaluated` with `reason: "unchained_prefix"` and
`ok: false`, alongside `entries_checked` and `entries_unchecked` naming the
covered range. The enabling call is itself audited, so coverage begins at
the `workspace.set_profiles` entry that switched the chain on.

**This verdict is permanent for that workspace.** `prev_hash` is written
when an entry is appended and there is no operation that back-fills it, so
the historical entries never enter the chain and `verify_chain` never
returns `verified` for a log that predates its chain. That is the correct
answer: the local chain holds no evidence about those entries.

Evidence for them has to come from outside the chain, which is what the
import procedure above provides. Receipts obtained that way are checked
with `audit.verify_receipt`, one entry at a time, and a deployment that
needs assurance over the historical range should record those receipts
rather than expect the chain verdict to change. A workspace that must have
one clean chain-level answer over its whole life has to enable
`audit-scitt/1.0` at creation.

See SPECIFICATION.md §10.2 for the normative rule.

---

## 7. Anchoring

A SCITT log root MAY be anchored to other transparency systems
(blockchain, RFC 3161 timestamp authority, immutable object store).
Anchoring is the SCITT working group's concern, not CHAP's; whatever
SCITT decides is what CHAP gets.

---

## 8. Error codes

| Code      | Meaning                                                |
|-----------|--------------------------------------------------------|
| `-32080`  | SCITT transparency service unreachable.                |
| `-32081`  | Statement rejected by transparency service.            |
| `-32082`  | Receipt verification failed.                           |

---

## 9. References

- [IETF SCITT working group](https://datatracker.ietf.org/wg/scitt/about/)
- [RFC 9943. An Architecture for Trustworthy and Transparent Digital Supply Chains](https://www.rfc-editor.org/rfc/rfc9943)
- [RFC 9052. CBOR Object Signing and Encryption (COSE)](https://datatracker.ietf.org/doc/html/rfc9052)
- [RFC 8785. JSON Canonicalization Scheme (JCS)](https://datatracker.ietf.org/doc/html/rfc8785)

---

## 10. Composition notes

- **With `security-signed`:** recommended. The deployment signs
  statements under its own key model.
- **With `identity-oidc` / `identity-vc`:** the Coordinator leaves
  the statement signature to the deployment, which can sign under the
  identity binding it uses. The transparency service may verify the
  issuer's identity chain.
- **Independence from MCP/A2A:** MCP and A2A have their own audit
  surfaces; the SCITT audit covers the CHAP layer specifically.
  Cross-protocol audit is by *citation*, not by encapsulation.
