# Profile: `identity-oidc`

**Profile id:** `identity-oidc/1.0` · **Depends on:** Core; pairs with `security-signed`.

Bind human Participant identities to OIDC ID tokens. The CHAP
signing key (advertised in `security-signed`) is bound to the
session via the OIDC `cnf.jwk` claim (RFC 7800).

CHAP introduces no identity protocol. This profile is the recommended
way to use OIDC with CHAP, but a deployment is free to use any OIDC
flow that produces a token suitable for the bindings below.

---

## 1. Standards reused (no reinvention)

| Need                       | Standard                                                                                          |
|----------------------------|---------------------------------------------------------------------------------------------------|
| Authentication             | [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)                  |
| Token-to-key binding       | [RFC 7800 `cnf.jwk`](https://datatracker.ietf.org/doc/html/rfc7800)                               |
| Step-up authentication     | OIDC `prompt=login`, `acr_values`, `auth_time`                                                    |
| Scope                      | OIDC / OAuth 2.0 scopes                                                                           |
| Discovery                  | OIDC Discovery / JWKS                                                                             |

---

## 2. The binding flow

```
Client (browser/app) generates Ed25519 keypair K
   │
   ▼
Client → IdP: OIDC authn request with cnf.jwk = pub(K)
   │
   ▼
IdP authenticates user (password, MFA, …)
   │
   ▼
IdP → Client: ID token containing cnf.jwk = pub(K), auth_time = T0
   │
   ▼
Client → Coordinator: participant.join with oidc_token = ID token
   │
   ▼
Coordinator: deployment's verifier checks the token; Coordinator pins cnf.jwk
   │
   ▼
Client → Coordinator: CHAP messages signed with K (security-signed profile)
```

The keypair is generated locally and never leaves the client. The
public half is delivered to the IdP via the `cnf.jwk` request
parameter (or its equivalent in the IdP's supported flow). The IdP
echoes it back inside the ID token; the Coordinator pins it.

The client presents the ID token as `oidc_token` on `participant.join`.
The deployment's verifier (`verifyOidcToken` or `verify_oidc_token`)
checks signature, expiry and audience and returns the claims; a
rejected token refuses the join with `-32403`. The Coordinator keeps
`sub`, `auth_time` and `acr`, and pins `cnf.jwk` as the participant's
key, under its `kid` or, where it has none, its RFC 7638 thumbprint.

An identity provider that cannot put the key in `cnf.jwk` can still
attest it through the nonce. The client asks for the token with `nonce`
set to the key's RFC 7638 thumbprint (SHA-256 over `{crv, kty, x}` for an
OKP key, or `{crv, kty, x, y}` for an EC key, base64url without padding),
and joins with the key in `jwks`. Where the verified claims carry no
`cnf.jwk` and a nonce, the Coordinator pins the join's key whose
thumbprint the nonce is.

A token that carries `cnf.jwk` or a nonce names the one key its holder
signs with. Every key in the join's `jwks` MUST be that key, the same `kid`
and thumbprint, and a join bringing any other is refused with `-32404`,
"The join's keys are not the key the token binds". A token that names no
key admits no key of the join's own: a new person's join that carries
`jwks` with such a token is refused with `-32404`, unless an invitation
pins those keys (SPECIFICATION.md §6.3.3). A token taken from its holder
therefore admits no key of the taker's.

Where a verifier is configured and the workspace advertises this profile,
a join of type `human` MUST carry an `oidc_token`, and is refused with
`-32403` otherwise; a person who joined with no token would sit in a
workspace that says its people are verified. Where `identity-vc/1.0`
also applies, a presentation in its place will do. Agents and services
join without one. Once advertised, the profile is never removed:
`workspace.set_profiles` that leaves it out is refused with `-32011`, and
one that adds it where no verifier is configured with `-32602`
(SPECIFICATION §6.5).

A token binds only to the participant it belongs to:

- Where the token carries `chap_participant_uri`, it MUST equal the
  join's `from`.
- A new member of type `human` is named by the token: its
  `chap_participant_uri` is the join's `from`, or an admin's invitation for
  that URI names the token's `sub` as `oidc_sub` (SPECIFICATION.md
  §6.3.3). A provider vouches for its user, and only the token or an
  invitation says which participant that user is: without this any user
  of the provider would take any name not yet taken.
- A join under the name of an existing member is accepted with a token
  only when the token's `sub` is the member's recorded subject. For a
  member with no recorded subject, the token's `chap_participant_uri`
  MUST name the member.

Any other such join is refused with `-32404`, and nothing about the
member changes. A join under an existing member's name that binds no key
and brings only keys the member does not hold is refused with `-32011`
(SPECIFICATION.md §6.3.2). The Coordinator compares `sub` alone, so a
verifier that accepts more than one issuer returns a `sub` unique across
them, for instance prefixed with `iss`.

The Coordinator records the `participant.join` call with `oidc_token`
replaced by `sha256:` and the hex SHA-256 of the token's UTF-8 bytes, so
the token never reaches the chain, where `audit.read` would return it to
any reader, and its holder can still match it to the entry.

---

## 3. Token shape

A CHAP-aware OIDC ID token:

```json
{
  "iss": "https://idp.example.org",
  "sub": "user-7f3c2a8e",
  "aud": "chap-coordinator-prod",
  "iat": 1747476000,
  "exp": 1747479600,
  "auth_time": 1747476000,
  "acr": "urn:example:authn:mfa",

  "email": "alice@example.org",
  "chap_participant_uri": "human:alice@example.org",

  "cnf": {
    "jwk": { "kty": "OKP", "crv": "Ed25519", "kid": "k-…", "x": "…" }
  }
}
```

Required for CHAP binding: `iss`, `aud`, `exp`, `sub`, `auth_time`,
`cnf.jwk` with a `kid`, and either `chap_participant_uri` or a
Coordinator-side mapping from `sub` to a CHAP URI. Neither reference
Coordinator keeps such a mapping; a verifier can add
`chap_participant_uri` to the claims it returns. A first join whose
token carries neither binds its `sub` to whatever `from` the join
names.

---

## 4. Step-up authentication

Step-up applies where the Coordinator enforces it (`enforceStepUp` or
`enforce_step_up`), to humans and OIDC-bound members calling
`control.*`, `workspace.set_profiles`, `participant.rotate_key` or
`participant.revoke_key`. Those calls need a fresh `auth_time`. A
workspace created with `min_acr` also requires the token's `acr` to
equal it. The default window is 5 minutes; the workspace's descriptor
publishes its configured value.

```
auth_time_age = now() - id_token.auth_time
if (privileged_method && auth_time_age > workspace.step_up_window_sec):
    return error(-32402, "step_up_required", { window_sec: ... })
```

The client recovers by presenting a fresh token with `participant.join`
and retrying; `prompt=login` at the IdP is the usual way to get one. A
member with no recorded subject can present a token only if its
`chap_participant_uri` names the member (§2). A member's refusal with
`-32402` is recorded (SPECIFICATION §10.1).

---

## 5. Scope and role

OIDC scopes do not directly map to CHAP roles. The pattern is:

1. OIDC scope authorises the bearer to *be* a CHAP participant.
2. The workspace's policy decides what that participant can do.

Example mapping:

```
OIDC scope            CHAP role        Permitted methods
─────────────────────────────────────────────────────────
chap.user              reviewer        decide.*, abstain.*, escalate.*
chap.user              drafter         task.*, message.*
chap.admin             admin           control.*, workspace.*
chap.audit             auditor         audit.* (read-only)
```

The mapping is deployment-specific and lives in the workspace
policy file. The protocol provides only the hooks.

---

## 6. Service authentication

For agents and services (no human at a keyboard):

| Option                       | When                                                |
|------------------------------|------------------------------------------------------|
| SPIFFE SVID                  | Service mesh (recommended)                          |
| OAuth 2.0 client credentials | Direct API access                                   |
| mTLS with private CA         | Inside organisational network                       |

In all three, the workload's identity binds to its CHAP signing key
via the same `cnf.jwk` mechanism.

---

## 7. Logout

```json
{
  "method": "participant.leave",
  "params": {
    "workspace": "wsp_demo",
    "from":      "human:alice@example.org",
    "to":        "service:coordinator@example.org",
    "ts":        "2026-05-17T17:00:00Z",
    "reason":    "logout"
  }
}
```

The client SHOULD additionally discard the local private key on
logout. The Coordinator records the leave in the audit log and
declines further messages purporting to come from the participant
with the now-discarded key.

---

## 8. Error codes

| Code      | Meaning                                                  |
|-----------|----------------------------------------------------------|
| `-32402`  | Step-up authentication required.                         |
| `-32403`  | ID token invalid (signature, expiry, audience), or a person's join with no token where this profile applies (§2). |
| `-32404`  | The token does not bind to this participant: its `chap_participant_uri` names another participant, or a join under an existing member's name carries a token that is not for that member (§2). |
| `-32405`  | Required OIDC scope not present. Allocated; neither reference Coordinator returns it. |

---

## 9. References

- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)
- [RFC 7800. Proof-of-Possession Key Semantics for JWTs](https://datatracker.ietf.org/doc/html/rfc7800)
- [SPIFFE](https://spiffe.io), for service-to-service identity

---

## 10. Composition notes

- **With `security-signed`:** the OIDC binding pins which Ed25519
  key the participant signs with.
- **With `audit-scitt`:** a SCITT statement's issuer is the `issuer`
  parameter of `audit.submit_to_scitt`.
- **With `control`:** step-up is the gate for privileged ops.
