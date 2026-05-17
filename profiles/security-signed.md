# Profile: `security-signed`

**Profile id:** `security-signed/1.0` · **Depends on:** Core

Add Ed25519 message signatures and JCS canonicalisation to CHAP
Core. This is the right profile for cross-trust-boundary deployments
(humans across organisations, agents from multiple vendors,
deployments subject to non-repudiation requirements).

This profile does **not** define identity — pair it with
[`identity-oidc`](./identity-oidc.md) or
[`identity-vc`](./identity-vc.md) to bind signing keys to
real-world principals.

---

## 1. What this profile adds

A single new field on every envelope:

```json
{
  "jsonrpc": "2.0",
  "id": "01HZ…",
  "method": "task.create",
  "params": { "...": "..." },
  "sig": "ed25519:k-2026-05-17a:V8M2…q0kg=="
}
```

The `sig` field is at the top level of the envelope (outside
`params`) so it can be elided during canonicalisation.

---

## 2. Standards reused

This profile is a thin wrapper over existing standards:

| Concern          | Standard                                                  |
|------------------|-----------------------------------------------------------|
| Signature algorithm | [Ed25519 — RFC 8032](https://datatracker.ietf.org/doc/html/rfc8032) |
| Canonical bytes  | [JCS — RFC 8785](https://datatracker.ietf.org/doc/html/rfc8785)  |
| Signature tag    | `ed25519:<kid>:<base64-signature>` |
| Key advertisement | [JSON Web Key — RFC 7517](https://datatracker.ietf.org/doc/html/rfc7517) |

---

## 3. The sign-and-verify recipe

### Signing

```
canonical = JCS( envelope with `sig` field removed )
sig_bytes = Ed25519_sign( canonical, private_key )
envelope.sig = "ed25519:" + kid + ":" + base64(sig_bytes)
```

### Verifying

```
sig = envelope.sig
kid, sig_b64 = parse(sig)            // split "ed25519:<kid>:<b64>"
pubkey = lookup(envelope.params.from, kid, envelope.params.ts)
canonical = JCS( envelope with `sig` field removed )
return Ed25519_verify( canonical, base64_decode(sig_b64), pubkey )
```

The public key is looked up by `(from, kid, ts)` — the key that was
valid for that participant at that timestamp. This makes historical
verification work after key rotation.

---

## 4. Key registration

A participant's public keys are advertised at `participant.join`:

```json
{
  "method": "participant.join",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T09:00:00Z",
    "type":      "human",
    "display_name": "Alice",
    "jwks": {
      "keys": [
        {
          "kty": "OKP",
          "crv": "Ed25519",
          "kid": "k-2026-05-17a",
          "x":   "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"
        }
      ]
    }
  },
  "sig": "ed25519:k-2026-05-17a:…"
}
```

The participant's first announce is self-signed (trust-on-first-use,
mediated by transport-level authentication). Subsequent keys are
introduced by signed `participant.rotate_key` messages.

---

## 5. Key rotation

```json
{
  "method": "participant.rotate_key",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T20:00:00Z",
    "old_kid":   "k-2026-05-17a",
    "new_jwk": {
      "kty": "OKP",
      "crv": "Ed25519",
      "kid": "k-2026-05-17b",
      "x":   "…"
    }
  },
  "sig": "ed25519:k-2026-05-17a:…"
}
```

The rotation message MUST be signed with the **old** key. The
Coordinator marks the old key as `valid_until: ts of rotation` and
the new key as `valid_from: ts of rotation`.

---

## 6. Revocation

```json
{
  "method": "participant.revoke_key",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:admin@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T22:00:00Z",
    "target_uri": "human:alice@example.org",
    "kid":       "k-2026-05-17a",
    "reason":    "suspected_compromise"
  },
  "sig": "ed25519:admin-key:…"
}
```

A revoked key remains valid for verifying messages dated **before**
the revocation. Messages dated after are rejected with `-32070`.

---

## 7. Error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32070`  | Signature verification failed.                   |
| `-32071`  | No known key matching `from` + `kid` + `ts`.     |
| `-32072`  | Key has been revoked.                            |
| `-32073`  | Rotation message not signed with old key.        |

---

## 8. Test vectors

See [`../conformance/test-vectors.md`](../conformance/test-vectors.md) §1 and §2 for canonical
inputs/outputs against RFC 8032 test vector 1.

---

## 9. Composition notes

- **With `identity-oidc`:** the OIDC `cnf.jwk` claim binds the
  signing key to the human's session; `participant.join` references
  the bound JWK.
- **With `audit-scitt`:** signed envelopes become SCITT statements
  with the participant's key as the SCITT identity.
- **With `core`:** Core's audit log records the full signed
  envelope verbatim, so the chain of signatures is recoverable from
  the log without any additional storage.
