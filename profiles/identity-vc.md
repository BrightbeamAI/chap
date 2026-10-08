# Profile: `identity-vc`

**Profile id:** `identity-vc/1.0` · **Depends on:** Core; pairs with `security-signed`.

Bind Participant identities to [W3C Verifiable Credentials 2.0](https://www.w3.org/TR/vc-data-model-2.0/).
Use this profile when richer or cross-organisational identity claims
are required than OIDC tokens conveniently express, regulated
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
Client → Coordinator: participant.join with vc_presentation = VP
   │
   ▼
Deployment's verifier: checks issuer signatures, VC schema, holder
       binding; resolves the DID; returns pub(K) as cnf_jwk
   │
   ▼
Coordinator pins cnf_jwk as the participant's CHAP signing key
   │
   ▼
Client → Coordinator: CHAP messages signed with K (security-signed profile)
```

The VP is the analogue of the OIDC ID token. The deployment's verifier
checks the VP, and the Coordinator pins the key it returns.

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
(`verificationMethod` identifies it). The deployment's verifier
resolves the DID and returns the key as `cnf_jwk`, which the
Coordinator pins. The challenge and domain prevent replay.

A join under the name of an existing member is accepted with a
presentation only when the presentation's holder is the member's
recorded holder. Any other such join, including one for a member with
no recorded holder, is refused with `-32411`, and nothing about the
member changes.

---

## 5. Participant URI

When `identity-vc` is in use, a Participant URI MAY use a DID
authority:

```
human:alice@example.org      # OIDC-bound (typical)
human:did:example:alice      # DID-bound (VC)
```

The deployment's verifier resolves the DID and returns the key as
`cnf_jwk`, which the Coordinator pins. The Coordinator does not compare
`vc_holder` with the participant URI.

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

The deployment's verifier checks the credential's status at
presentation time. The Coordinator SHOULD re-check periodically for
long-lived sessions, and a revoked credential MUST result in the
participant being removed from the workspace; neither reference does
either yet (milestone 0.6).

---

## 8. Error codes

| Code      | Meaning                                                |
|-----------|--------------------------------------------------------|
| `-32410`  | VP signature verification failed.                      |
| `-32411`  | Holder binding failed: a join under an existing member's name presents another holder, or the member has no recorded holder (§4). |
| `-32412`  | VC has been revoked.                                   |
| `-32413`  | Credential schema not recognised by this workspace.    |

Two further conditions are described by this profile but are **not yet
implemented** by either reference implementation, so no code is assigned:
an issuer the workspace does not trust, and a required credential claim
that was not disclosed. Implementations encountering them today reject the
presentation as invalid (`-32410`). Assigning codes is left to a revision of
this profile.

Both reference coordinators return `-32410` whenever the deployment's
verifier rejects a presentation, and `-32411` for the holder rule in §4.
`-32412` and `-32413` are allocated for the verifier's specific failures
and returned by neither today.

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
- **With `audit-scitt`:** a SCITT statement's issuer is the `issuer`
  parameter of `audit.submit_to_scitt`, which can be the holder's DID.
- **With `identity-oidc`:** they coexist; pick the right one per
  participant based on the trust model.
