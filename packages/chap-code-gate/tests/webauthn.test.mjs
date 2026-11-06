// A reviewer's approval signed with a passkey, checked by the gate itself
// against the key its trust policy pins, as a WebAuthn relying party checks
// an assertion: the request it signed, the origin, the relying party, the
// person verified, and the ES256 signature.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, sign as cryptoSign } from "node:crypto";
import { canonicalize, fingerprint as deskFingerprint, thumbprint } from "@brightbeamai/chap-server/client";
import { envelopeVerifies, fingerprint, policyFromTrust } from "../lib/gate.mjs";

const sha256 = (b) => createHash("sha256").update(b).digest();
const ORIGIN = "http://localhost:8791";

async function passkey() {
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
  const { x, y } = publicKey.export({ format: "jwk" });
  const jwk = { kty: "EC", crv: "P-256", x, y, alg: "ES256", kid: await thumbprint({ kty: "EC", crv: "P-256", x, y }), use: "sig", webauthn: { rp_id: "localhost", origins: [ORIGIN] } };
  /** An envelope signed as an authenticator would, with what each argument says. */
  const sign = (envelope, { flags = 0x05, origin = ORIGIN, rpId = "localhost", over = envelope } = {}) => {
    const authData = Buffer.alloc(37);
    sha256(rpId).copy(authData, 0);
    authData[32] = flags;
    authData.writeUInt32BE(1, 33);
    const clientData = Buffer.from(JSON.stringify({ type: "webauthn.get", challenge: sha256(canonicalize(over)).toString("base64url"), origin, crossOrigin: false }));
    const signature = cryptoSign("sha256", Buffer.concat([authData, sha256(clientData)]), { key: privateKey, dsaEncoding: "der" });
    return { ...envelope, sig: `webauthn:${jwk.kid}:${[authData, clientData, signature].map((b) => b.toString("base64url")).join(".")}` };
  };
  return { jwk, sign };
}

const approval = { jsonrpc: "2.0", id: "d1", method: "decide.approve", params: { workspace: "wsp_gate", from: "human:you@local", task_id: "tsk_1", approved_artefact_digest: "sha256:00" } };

test("an approval a passkey signed verifies under the pinned key, and one it did not sign does not", async () => {
  const key = await passkey();
  assert.deepEqual(envelopeVerifies(key.sign(approval), [key.jwk]), { ok: true, kid: key.jwk.kid, jwk: key.jwk });
  const refused = (envelope, keys = [key.jwk]) => envelopeVerifies(envelope, keys).reason;
  assert.equal(refused({ ...key.sign(approval), params: { ...approval.params, task_id: "tsk_2" } }), "the passkey signed another request");
  assert.equal(refused(key.sign(approval, { flags: 0x01 })), "the passkey signed without verifying the person");
  assert.equal(refused(key.sign(approval, { origin: "http://localhost:9999" })), "the passkey signed on http://localhost:9999, which the key does not allow");
  assert.equal(refused(key.sign(approval, { rpId: "evil.example" })), "the passkey signed for another relying party");
  const other = await passkey();
  assert.equal(refused(other.sign(approval)), `no pinned passkey with kid ${other.jwk.kid}`);
  assert.equal(refused({ ...other.sign(approval), sig: other.sign(approval).sig.replace(other.jwk.kid, key.jwk.kid) }), "the passkey's signature does not verify");
  assert.equal(refused({ ...approval, sig: `webauthn:${key.jwk.kid}:abc` }), "the passkey's assertion is not three base64url parts");
});

test("a trust policy pins passkeys for reviewers, never for agents, and prints their fingerprints", async () => {
  const key = await passkey();
  const policy = policyFromTrust({ chap_trust: 1, workspace: "wsp_gate", reviewers: { "human:you@local": { name: "You", keys: [key.jwk] } }, agents: {} }, "chap-trust.json");
  assert.deepEqual(policy.reviewers["human:you@local"], [key.jwk]);
  assert.throws(() => policyFromTrust({ chap_trust: 1, reviewers: {}, agents: { "agent:coder": [key.jwk] } }, "chap-trust.json"), /chap-trust\.json: agents\[agent:coder\] is not a list of Ed25519 public keys/);
  const point = Buffer.concat([Buffer.from([4]), Buffer.from(key.jwk.x, "base64url"), Buffer.from(key.jwk.y, "base64url")]);
  assert.equal(fingerprint(key.jwk), `SHA256:${sha256(point).toString("base64").replace(/=+$/, "")}`, "the fingerprint is of the uncompressed point");
  assert.equal(await deskFingerprint(key.jwk), fingerprint(key.jwk), "the desk's badge shows what trust prints");
});
