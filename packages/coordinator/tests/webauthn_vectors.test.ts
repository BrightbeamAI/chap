/**
 * conformance/webauthn-vectors.json: a WebAuthn assertion signs a request
 * under security-signed/1.0, and each check the relying party makes refuses
 * an assertion that fails it (6 November review, item 3).
 *
 * Both coordinators verified ed25519: tags alone, so a person whose key sits
 * in a browser's authenticator could not sign at all. Mirrors
 * test_webauthn_vectors.py.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash, createPrivateKey, sign } from "node:crypto";
import { Coordinator } from "../src/coordinator.js";
import { canonicalize } from "../src/canonical.js";
import { isWebAuthnKey, parseWebAuthnAssertion } from "../src/crypto.js";

const VECTORS = JSON.parse(readFileSync(
  new URL("../../../conformance/webauthn-vectors.json", import.meta.url), "utf-8"));

function ready() {
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  for (const env of VECTORS.setup) {
    const r = c.dispatch(env) as any;
    assert.equal(r.error, undefined, JSON.stringify(r.error));
  }
  return c;
}

test("the vector's assertion signs its request", () => {
  const c = ready();
  const r = c.dispatch(VECTORS.accepted.envelope) as any;
  assert.equal(r.error, undefined, JSON.stringify(r.error));
  assert.equal(r.result.state, "created");
});

test("the vector's parts are what its tag carries", () => {
  const { envelope, challenge, authenticator_data_hex, client_data_json, signature_hex } = VECTORS.accepted;
  const { sig, ...unsigned } = envelope;
  assert.equal(createHash("sha256").update(canonicalize(unsigned)).digest("base64url"), challenge);
  const assertion = parseWebAuthnAssertion(sig.split(":")[2])!;
  assert.equal(assertion.authenticatorData.toString("hex"), authenticator_data_hex);
  assert.equal(assertion.clientDataJSON.toString("utf-8"), client_data_json);
  assert.equal(assertion.signature.toString("hex"), signature_hex);
  assert.equal(JSON.parse(client_data_json).challenge, challenge);
  assert.ok(isWebAuthnKey(VECTORS.jwk));
});

for (const vector of VECTORS.refused) {
  test(`refused: ${vector.name}`, () => {
    const c = ready();
    const before = c.workspaces.get("wsp_webauthn")!.audit.length;
    const r = c.dispatch(vector.envelope) as any;
    assert.deepEqual(r.error, vector.error);
    assert.equal(c.workspaces.get("wsp_webauthn")!.audit.length, before);
  });
}

test("a key without a WebAuthn relying party, or of another curve, is not a WebAuthn key", () => {
  const { webauthn, ...plain } = VECTORS.jwk;
  assert.equal(isWebAuthnKey(plain), false);
  assert.equal(isWebAuthnKey({ ...VECTORS.jwk, crv: "P-384" }), false);
  assert.equal(isWebAuthnKey({ ...VECTORS.jwk, alg: "ES384" }), false);
  assert.equal(isWebAuthnKey({ ...VECTORS.jwk, webauthn: { ...webauthn, origins: "https://desk.example.org" } }), false);
  assert.equal(isWebAuthnKey({ ...VECTORS.jwk, x: VECTORS.jwk.x + "=" }), false);
});

for (const [named, webauthn] of [["no origins", { rp_id: "example.org" }],
                                 ["an empty list of origins", { rp_id: "example.org", origins: [] }]] as const) {
test(`a key that lists ${named} signs nothing`, () => {
  // Sign afresh with the vector's private key, for a key that names no origin:
  // whoever registers such a key would otherwise decide that any origin may
  // sign for it.
  const jwk = { ...VECTORS.jwk, kid: "any-origin", webauthn };
  const d = Buffer.from(VECTORS.private_scalar_hex, "hex").toString("base64url");
  const privateKey = createPrivateKey({ key: { ...jwk, d }, format: "jwk" });
  const c = new Coordinator({ deterministicIds: true, deterministicClock: true });
  c.dispatch({ ...VECTORS.setup[0] });
  c.dispatch({ jsonrpc: "2.0", id: "j", method: "participant.join",
               params: { workspace: "wsp_webauthn", from: "human:carol@example.org", type: "human",
                         jwks: { keys: [jwk] } } });
  const request = { jsonrpc: "2.0" as const, id: "r", method: "task.create",
                    params: { workspace: "wsp_webauthn", from: "human:carol@example.org", kind: "k", input: {},
                              assignee: "human:carol@example.org" } };
  const clientDataJSON = Buffer.from(JSON.stringify({ type: "webauthn.get",
    challenge: createHash("sha256").update(canonicalize(request)).digest("base64url"),
    origin: "https://anywhere.example" }));
  const authenticatorData = Buffer.concat([createHash("sha256").update("example.org").digest(),
                                           Buffer.from([0x05, 0, 0, 0, 7])]);
  const signature = sign("sha256", Buffer.concat([authenticatorData, createHash("sha256").update(clientDataJSON).digest()]),
                         { key: privateKey, dsaEncoding: "der" });
  const sig = `webauthn:any-origin:${[authenticatorData, clientDataJSON, signature].map((b) => b.toString("base64url")).join(".")}`;
  const r = c.dispatch({ ...request, sig }) as any;
  assert.deepEqual(r.error, { code: -32070, message: "WebAuthn key lists no origin" });
});
}

test("a tag with more than three parts is malformed", () => {
  const c = ready();
  const r = c.dispatch({ ...VECTORS.accepted.envelope, sig: VECTORS.accepted.envelope.sig + ":x" }) as any;
  assert.deepEqual(r.error, { code: -32070, message: "Malformed signature tag" });
});
