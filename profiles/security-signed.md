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

### Where signatures are required

A workspace requires signatures when it advertises this profile, or when
its Coordinator is configured to require them (`requireSignatures`,
`require_signatures`). Advertising the profile is enough: a workspace an
admin moved onto it with `workspace.set_profiles`, or one whose store a
Coordinator without the option reopened, verifies every call as one
created under the option does. A `workspace.set_profiles` that would stop
advertising the profile MUST be refused with `-32011`.

The workspace's `signature_policy`, a `workspace.create` param that falls
back to the Coordinator's `signaturePolicy` or `signature_policy` option,
says which calls are verified, and `workspace.describe` returns it:

| Policy | Verified |
|---|---|
| `"all"` (the default) | Every call except `workspace.create` and `participant.join`. |
| `"humans"` | The same, except a call that carries no `sig` and names in `from` an existing member whose `type` is `agent`. |

Under `"humans"` people and services sign, and an agent on the server's
own transport, such as `/mcp`, works unsigned. A signed call is verified
whoever sends it, and a call from a sender who is not a member is
verified, so a person cannot be impersonated by leaving a signature off,
and an unsigned call never decides a review that requires a person (review
profile §3.1). The policy relies on the transport to establish who the
agent is.

A participant that signs its calls, every one under `"all"` and every one
but an agent under `"humans"`, joins a workspace that requires signatures
with a key: one in its `jwks`, one an identity profile binds, or keys an
admin's invitation pins (SPECIFICATION §6.3.3). A join that brings none is
refused with `-32602`, so no member holds a name it cannot sign for.

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
verification work after key rotation. A tag of more than three parts is
refused with `-32070`, "Malformed signature tag".

The signature is standard base64 (RFC 4648 §4), padded, and nothing else:
no URL-safe alphabet, no missing padding, no whitespace and nothing after
the padding, and no stray bits in the last character. A signature in any
other spelling is refused with `-32070`, "Signature failed verification",
so one signature has one spelling and every Coordinator reads it alike. An
`ed25519:` tag naming a key that is not an Ed25519 JWK (`kty` `"OKP"`, `crv`
`"Ed25519"`) is refused with "Key `<kid>` is not an Ed25519 key".

### WebAuthn signatures

A person whose key is held by a browser's authenticator signs through
WebAuthn, which cannot sign arbitrary bytes. The request's digest goes in
as the assertion's challenge, and the tag carries the whole assertion:

```
webauthn:<kid>:<authenticatorData>.<clientDataJSON>.<signature>
```

Each part is unpadded base64url. The key, registered at
`participant.join` like any other, is a P-256 JWK with a `webauthn` member:

```json
{ "kty": "EC", "crv": "P-256", "x": "…", "y": "…", "alg": "ES256",
  "kid": "…", "use": "sig",
  "webauthn": { "rp_id": "example.org", "origins": ["https://desk.example.org"] } }
```

`rp_id` names the relying party, and `origins` the origins an assertion
may come from; a key MUST list at least one. A client MAY set a key's
`kid` to its RFC 7638 thumbprint, as the reference desk does for every key
it makes. The Coordinator makes these checks of the relying party's
assertion procedure (WebAuthn Level 3 §7.2), in this order, and refuses an
assertion that fails one with `-32070` and the message given:

1. The key lists at least one origin ("WebAuthn key lists no origin").
   Whoever registers a key cannot leave the origin unchecked, so a page on
   any other origin, another port of `localhost` included, cannot sign.
2. `clientDataJSON` nests no deeper than 64 levels ("WebAuthn
   clientDataJSON nests deeper than 64 levels") and is a JSON object
   ("WebAuthn clientDataJSON is not a JSON object") whose `type` is
   `"webauthn.get"` ("WebAuthn clientDataJSON type is not webauthn.get").
3. Its `challenge` is the base64url SHA-256 of the canonical bytes an
   `ed25519:` signature would sign ("WebAuthn challenge is not the digest
   of this request"). This binds the assertion to the request.
4. Its `origin` is one of the key's `origins` ("WebAuthn origin is not one
   the key allows"), and it carries no `crossOrigin: true` and no
   `topOrigin` ("WebAuthn assertion was made in a cross-origin frame"): an
   assertion made in a frame another origin embeds is not the key holder's
   page acting.
5. `authenticatorData` holds at least 37 bytes ("WebAuthn
   authenticatorData is too short"), and its first 32 are the SHA-256 of
   the key's `rp_id` ("WebAuthn rpIdHash does not match the key's
   rp_id").
6. Its flags set both UP (`0x01`) and UV (`0x04`), so a person was present
   and verified ("WebAuthn assertion lacks user presence or
   verification"); BS (`0x10`) only with BE (`0x08`) ("WebAuthn assertion
   is backed up but not backup eligible"); and not AT (`0x40`), since an
   assertion carries no attested credential data ("WebAuthn assertion
   carries attested credential data").
7. `authenticatorData` ends after its 37 bytes where ED (`0x80`) is clear,
   and runs on past them where ED is set ("WebAuthn authenticatorData does
   not end where its flags say").
8. The ES256 signature, DER-encoded, verifies over `authenticatorData`
   followed by the SHA-256 of `clientDataJSON` ("Signature failed
   verification").

The Coordinator keeps no signature counter, and reads no extension data
beyond its presence: a deployment that needs either checks it in front of
the Coordinator.

A `webauthn:` tag naming a key that is not such a JWK is refused with
"Key `<kid>` is not a WebAuthn key", and a tag whose assertion is not three
base64url parts with "Malformed signature tag". The Python coordinator
verifies through the `cryptography` package; without it, any signature is
refused with `-32070` and a message naming the `crypto` extra.

### A signed request takes effect once

Every reader of the log holds a copy of each signed call on it, signature
and all. A Coordinator MUST NOT evaluate a signed request whose signed
content, the call without its `sig`, has the same canonical form as a call
the log holds as accepted. It answers `-32075`, "Accepted at seq N; a
signed request takes effect once", with `data: {"accepted_at_seq": N}`,
and does not record the refusal. Without this a copy of a reviewer's
signed reject, taken from the log, could send back the same artefact again
in the reviewer's name once the agent resubmitted it. A client that is not
sure a signed call arrived can send it again unchanged: `-32075` says it
took effect, and where. SPECIFICATION §10.1 fixes where the check comes in
the order of checks, and what a signed copy of a recorded refusal is
answered with.

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
them is recorded and never verified. Keys advertised at join are trusted
on first use, and ignored where an identity profile pins a key. Later
keys arrive through `participant.rotate_key`, or through a later join
carrying the member's own token or presentation. A later join that
brings only keys the member does not hold, and no token or presentation
binding one, is refused with `-32011` (SPECIFICATION.md §6.3.2).

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
`valid_from` to its own clock. The new key takes a `kid` of its own: a
`new_jwk` whose `kid` the member already holds, the old key's included,
MUST be refused with `-32602`, since keys are found by `kid` and the new key
would be closed with the old one.

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
| `-32075`  | A signed request the log already holds as accepted (§3); it takes effect once. `data.accepted_at_seq` names its entry. Returned whether or not this profile is advertised. |

Refusals with `-32070` to `-32073` and `-32075` are never recorded; one
with `-32074` is recorded when the caller is a member.

---

## 8. Test vectors

See [`../conformance/test-vectors.md`](../conformance/test-vectors.md) §1 and §2 for canonical
inputs/outputs against RFC 8032 test vector 1, and §1a and
[`../conformance/webauthn-vectors.json`](../conformance/webauthn-vectors.json)
for a WebAuthn assertion and the refusal of each check.

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
