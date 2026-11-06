# WebAuthn signature vectors

`webauthn-vectors.json` is the shared fixture for WebAuthn signatures under
`security-signed/1.0` (`profiles/security-signed.md` §3). Both coordinator
test suites replay the same envelopes with deterministic ids and a
deterministic clock: `setup` creates a workspace that advertises
`security-signed/1.0` and joins two people, one with the vector's P-256
WebAuthn key and one with an Ed25519 key. The envelope under `accepted` is
signed with a WebAuthn assertion and takes effect. Each case under `refused`
fails one check and is answered with `error`, a `-32070` with the message
the profile gives for that check, and leaves the log as it was.

The cases other than `signature-altered`, `assertion-of-two-parts` and
`request-changed-after-signing` are signed afresh with the vector's key, so
the check named is the only one that fails. `private_scalar_hex` is the
key's private scalar, published so anyone can sign a further case; it is a
test key and signs nothing else. ECDSA signatures are randomised, so a case
signed again differs in its signature bytes and verifies the same way.

`conformance/test-vectors.md` §1a sets out the accepted assertion part by
part.
