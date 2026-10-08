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
key when it carries a `kid`, ignoring self-asserted `jwks`.

A token binds only to the participant it belongs to:

- Where the token carries `chap_participant_uri`, it MUST equal the
  join's `from`.
- A join under the name of an existing member is accepted with a token
  only when the token's `sub` is the member's recorded subject. For a
  member with no recorded subject, the token's `chap_participant_uri`
  MUST name the member.

Any other such join is refused with `-32404`, and nothing about the
member changes. A join under an existing member's name with neither a
token nor a presentation keeps the member as it is. The Coordinator
compares `sub` alone, so a verifier that accepts more than one issuer
returns a `sub` unique across them, for instance prefixed with `iss`.

The Coordinator records the `participant.join` call as sent,
`oidc_token` included, and `audit.read` returns it to any caller unless
the Coordinator requires read membership. Deployments SHOULD require
read membership and use short-lived tokens.

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
| `-32403`  | ID token invalid (signature, expiry, audience).          |
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
