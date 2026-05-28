# Profile: `audit-scitt`

**Profile id:** `audit-scitt/1.0` · **Depends on:** `security-signed`

The `audit-scitt` profile says: **the workspace's audit log is a
[SCITT](https://datatracker.ietf.org/wg/scitt/about/) transparency
service.** Every accepted CHAP envelope becomes a SCITT signed
statement; every accepted envelope produces a SCITT receipt that any
party can verify offline against the transparency service's signed
log root.

CHAP does not define its own transparency primitive; this profile
defers entirely to SCITT.

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
│                            │    participants)     │       │
│                            └──────────────────────┘       │
└──────────────────────────────────────────────────────────┘
```

The SCITT Transparency Service is its own component. It MAY be
operated by the same party as the Coordinator, by a third party
(notary), or by a federation of mutually-distrusting parties.

---

## 3. Statement format

Each accepted envelope is wrapped as a SCITT signed statement:

```
COSE_Sign1 {
  protected: {
    alg: -8   // Ed25519
    iss: <participant's URI>
    kid: <key id>
    cwt_claims: {
      sub: <workspace id>
      iat: <unix ts>
    }
    content-type: "application/chap+json;version=0.2"
  }
  payload: <JCS canonicalisation of the CHAP envelope>
  signature: <Ed25519 signature over the protected headers + payload>
}
```

The protected headers identify the workspace and the issuing
participant. The payload is the canonical CHAP envelope; receivers
can extract and process it normally.

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

## 5. What CHAP no longer defines

This profile **deletes** the following from CHAP itself:

- The bespoke `EvidenceEntry` schema.
- The `prev_hash` chain linkage rules.
- The custom `coord_sig` co-signature field.
- The custom `audit.checkpoint` and `audit.verify` semantics for chain integrity.

In their place: standard SCITT receipts and the standard SCITT
verification procedure.

CHAP retains `audit.read` (for browsing the log), but the underlying
storage and verification primitives are now SCITT's.

---

## 6. Importing pre-existing audit data

When adopting `audit-scitt` against an existing audit store (a
plain database log, a custom transparency log, or a different
append-only store), the recommended procedure:

1. For each historical envelope in arrival order, construct a SCITT
   signed statement whose payload is the envelope's JCS
   canonicalisation.
2. Preserve any pre-existing integrity metadata (chain hashes,
   coordinator signatures) inside the protected headers as
   informative fields.
3. Submit each statement to the SCITT transparency service.
4. Receipts for historical entries are issued retroactively; the
   resulting log is forward-verifiable from any historical point.

The historical entries remain auditable both via their original
provenance and via SCITT receipts. New entries are SCITT-only.

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
- [draft-ietf-scitt-architecture](https://datatracker.ietf.org/doc/draft-ietf-scitt-architecture/)
- [RFC 9052. CBOR Object Signing and Encryption (COSE)](https://datatracker.ietf.org/doc/html/rfc9052)
- [RFC 8785. JSON Canonicalization Scheme (JCS)](https://datatracker.ietf.org/doc/html/rfc8785)

---

## 10. Composition notes

- **Requires `security-signed`**: SCITT statements carry signatures
  whose semantics need a defined key model, that's what
  `security-signed` provides.
- **With `identity-oidc` / `identity-vc`:** SCITT statement
  signatures use whichever identity binding the workspace has
  configured. The transparency service may verify the issuer's
  identity chain.
- **Independence from MCP/A2A:** MCP and A2A have their own audit
  surfaces; the SCITT audit covers the CHAP layer specifically.
  Cross-protocol audit is by *citation*, not by encapsulation.
