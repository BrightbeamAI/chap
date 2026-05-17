# Profile: `identity-vc`

**Profile id:** `identity-vc/1.0` · **Depends on:** Core; pairs with `security-signed`.

Bind Participant identities to [W3C Verifiable Credentials 2.0](https://www.w3.org/TR/vc-data-model-2.0/).
Use this profile when richer or cross-organisational identity claims
are required than OIDC tokens conveniently express — regulated
professions, cross-org credentials, supply-chain attestations.

CHAP introduces no identity protocol. This profile is the recommended
way to use W3C VC with CHAP.

---

## 1. Standards reused

| Need                  | Standard                                                                |
|-----------------------|-------------------------------------------------------------------------|
| Credential format     | [W3C Verifiable Credentials Data Model 2.0](https://www.w3.org/TR/vc-data-model-2.0/) |
| Identifier            | [W3C Decentralized Identifiers (DIDs) 1.0](https://www.w3.org/TR/did-core/) |
| Presentation          | W3C Verifiable Presentations                                            |
| Suite for proofs      | Data Integrity Proofs (eddsa-rdfc-2022, ecdsa-rdfc-2019, etc.)         |

---

## 2. When to use VC over OIDC

| You need…                                                  | Use            |
|------------------------------------------------------------|----------------|
| Standard "who is this user in our IdP" identity            | `identity-oidc` |
| Attested professional or regulatory role (e.g. clinician)  | `identity-vc`   |
| Credential issued by a party other than the user's employer | `identity-vc`   |
| Cross-organisation identity with no shared IdP             | `identity-vc`   |
| Selective disclosure of credential fields                  | `identity-vc`   |

OIDC and VC can coexist in the same workspace; some participants
present OIDC tokens and others present VCs.

---

## 3. The binding flow

```
Client (browser/app) generates Ed25519 keypair K
   │
   ▼
Client constructs a Verifiable Presentation containing one or more VCs,
       signed with K (proof of possession) plus the issuer's signatures
   │
   ▼
Client → Coordinator: handshake — presents VP
   │
   ▼
Coordinator: verifies VP — issuer signatures, VC schema, holder binding
   │
   ▼
Coordinator pins pub(K) as the participant's CHAP signing key
   │
   ▼
Client → Coordinator: CHAP messages signed with K (security-signed profile)
```

The VP is the analogue of the OIDC ID token. The Coordinator does
exactly the same job: verify identity, extract the bound key, pin.

---

## 4. Sample VP

```json
{
  "@context": ["https://www.w3.org/ns/credentials/v2"],
  "type":     ["VerifiablePresentation"],
  "holder":   "did:example:alice",
  "verifiableCredential": [
    {
      "@context": ["https://www.w3.org/ns/credentials/v2"],
      "type":      ["VerifiableCredential", "ProfessionalRoleCredential"],
      "issuer":    "did:example:medical-board",
      "validFrom": "2025-01-01T00:00:00Z",
      "credentialSubject": {
        "id":              "did:example:alice",
        "role":            "registered-clinician",
        "registrationNo":  "RC-2025-00481",
        "specialty":       "internal-medicine"
      },
      "proof": { "...": "issuer's data-integrity proof" }
    }
  ],
  "proof": {
    "type":              "DataIntegrityProof",
    "cryptosuite":       "eddsa-rdfc-2022",
    "verificationMethod": "did:example:alice#key-1",
    "proofPurpose":      "authentication",
    "challenge":         "<CHAP nonce>",
    "domain":            "chap-coordinator.example.org",
    "proofValue":        "..."
  }
}
```

The presentation's `proof` binds the holder to the CHAP signing key
(`verificationMethod` identifies it). The challenge and domain
prevent replay.

---

## 5. Participant URI

When `identity-vc` is in use, a Participant URI MAY use a DID
authority:

```
human:[email protected]            # OIDC-bound (typical)
human:[email protected]                  # DID-bound (VC)
```

The Coordinator resolves the DID to look up verification methods.

---

## 6. Selective disclosure

W3C VC supports selective disclosure (SD-JWT, BBS+). CHAP can carry
either:

- A **full** credential (all claims visible).
- A **selectively-disclosed** presentation (only the claims the
  holder chose to reveal).

The Coordinator MAY require specific claims to be disclosed for
specific roles ("clinician role requires `registrationNo` to be
disclosed"). This is policy, not protocol.

---

## 7. Revocation

VCs use the standard W3C status mechanisms:

- StatusList2021 (most common)
- Issuer-side revocation registry

The Coordinator checks the credential's status at presentation
time, and SHOULD re-check periodically for long-lived sessions.
A revoked credential MUST result in the participant being removed
from the workspace.

---

## 8. Error codes

| Code      | Meaning                                                |
|-----------|--------------------------------------------------------|
| `-32410`  | VP signature verification failed.                      |
| `-32411`  | VC issuer not trusted by this workspace.               |
| `-32412`  | VC has been revoked.                                   |
| `-32413`  | Required credential claim not disclosed.               |
| `-32414`  | Holder binding (proof of possession) failed.           |

---

## 9. References

- [W3C Verifiable Credentials Data Model 2.0](https://www.w3.org/TR/vc-data-model-2.0/)
- [W3C DIDs 1.0](https://www.w3.org/TR/did-core/)
- [W3C VC Data Integrity 1.0](https://www.w3.org/TR/vc-data-integrity/)
- [IETF SD-JWT](https://datatracker.ietf.org/doc/draft-ietf-oauth-selective-disclosure-jwt/)

---

## 10. Composition notes

- **With `security-signed`:** the VP's `verificationMethod` binds
  the CHAP signing key.
- **With `audit-scitt`:** SCITT statements can carry the holder's
  DID as the issuer identifier — the audit chain remains valid
  across organisational boundaries.
- **With `identity-oidc`:** they coexist; pick the right one per
  participant based on the trust model.
