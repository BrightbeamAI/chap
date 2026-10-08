# Profile: `security-signed`

**Profile id:** `security-signed/1.0` · **Depends on:** Core

Add Ed25519 message signatures and JCS canonicalisation to CHAP
Core. This is the right profile for cross-trust-boundary deployments
(humans across organisations, agents from multiple vendors,
deployments subject to non-repudiation requirements).

This profile does **not** define identity, pair it with
[`identity-oidc`](./identity-oidc.md) or
[`identity-vc`](./identity-vc.md) to bind signing keys to
real-world principals.

---

## 1. What this profile adds

A single new field on every envelope except `participant.join` and
`workspace.create`:

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
| Signature algorithm | [Ed25519. RFC 8032](https://datatracker.ietf.org/doc/html/rfc8032) |
| Canonical bytes  | [JCS. RFC 8785](https://datatracker.ietf.org/doc/html/rfc8785), with the number restriction of SPECIFICATION §5.2 |
| Signature tag    | `ed25519:<kid>:<base64-signature>` |
| Key advertisement | [JSON Web Key. RFC 7517](https://datatracker.ietf.org/doc/html/rfc7517) |

---

## 3. The sign-and-verify recipe

### Signing

```
// JCS with the number restriction of SPECIFICATION §5.2
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

The public key is looked up by `(from, kid, ts)`: the key that was
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

`participant.join` and `workspace.create` are not signature-checked,
because the Coordinator holds no key for the caller yet; a `sig` on
them is recorded as sent and never verified. Keys advertised at join
are trusted on first use, and ignored where an identity profile pins a
key. Later keys arrive through `participant.rotate_key`.

---

## 5. Key rotation

`participant.rotate_key` and `participant.revoke_key` are Core methods,
answered whatever the workspace advertises; this profile adds the
signature requirement.

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
Coordinator sets the old key's `valid_until` and the new key's
`valid_from` to its own clock.

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

A call signed with a revoked key is refused with `-32072`, whatever
its `ts`. Calls the Coordinator accepted before the revocation stay on
the log.
Revoking another member's key needs the `admin` role, else `-32011`.

---

## 7. Error codes

| Code      | Meaning                                          |
|-----------|--------------------------------------------------|
| `-32070`  | Signature verification failed.                   |
| `-32071`  | No known key matching `from` + `kid` + `ts`.     |
| `-32072`  | Key has been revoked.                            |
| `-32073`  | Rotation message not signed with old key.        |
| `-32074`  | `approved_artefact_digest` does not match the artefact under review. Returned by `decide.*` whether or not this profile is advertised. |

Refusals with `-32070` to `-32073` are never recorded; one with
`-32074` is recorded when the caller is a member.

---

## 8. Test vectors

See [`../conformance/test-vectors.md`](../conformance/test-vectors.md) §1 and §2 for canonical
inputs/outputs against RFC 8032 test vector 1.

---

## 9. Composition notes

- **With `identity-oidc`:** the OIDC `cnf.jwk` claim binds the
  signing key to the human's session; `participant.join` references
  the bound JWK.
- **With `audit-scitt`:** a SCITT statement carries the signed envelope
  in its payload, and its issuer is the `issuer` parameter of
  `audit.submit_to_scitt`.
- **With `core`:** Core's audit log records the full signed
  envelope verbatim, so the chain of signatures is recoverable from
  the log without any additional storage.
