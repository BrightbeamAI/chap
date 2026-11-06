// A reviewer's approval signed with a passkey, checked as the coordinator
// checks it (security-signed/1.0 3), against a key the trust policy pins.
// The gate verifies the evidence on its own, so a commit's approval holds
// against the policy whatever the coordinator that recorded it accepted.
//
// The tag is webauthn:<kid>:<authenticatorData>.<clientDataJSON>.<signature>,
// each part unpadded base64url, and the key a P-256 JWK with a `webauthn`
// member naming the relying party and the origins assertions may come from.

import { createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";

const B64URL = /^[A-Za-z0-9_-]+$/;
const COORDINATE = /^[A-Za-z0-9_-]{43}$/;
const sha256 = (bytes) => createHash("sha256").update(bytes).digest();

/** Whether a value is a public key a passkey signs with: P-256 for ES256, naming its relying party. */
export function isWebAuthnKey(k) {
  const w = k?.webauthn;
  return !!k && typeof k === "object" && k.kty === "EC" && k.crv === "P-256" && COORDINATE.test(k.x ?? "") && COORDINATE.test(k.y ?? "")
    && (k.alg === undefined || k.alg === "ES256") && !!w && typeof w === "object" && typeof w.rp_id === "string" && w.rp_id !== ""
    && (w.origins === undefined || (Array.isArray(w.origins) && w.origins.every((o) => typeof o === "string")));
}

/**
 * Why the assertion `part` (the tag's last part) does not sign the request
 * whose canonical form, without its sig, is `canonical`, under `jwk`; null
 * when it does. The checks are a relying party's: the client data says it
 * is an assertion over this request, from an origin the key allows; the
 * authenticator data is for the key's relying party, with the person
 * present and verified; and the ES256 signature covers the authenticator
 * data and the hash of the client data.
 */
export function webauthnFailure(canonical, part, jwk) {
  const pieces = String(part).split(".");
  if (pieces.length !== 3 || pieces.some((p) => !B64URL.test(p) || p.length % 4 === 1)) return "the passkey's assertion is not three base64url parts";
  const [authData, clientDataJSON, signature] = pieces.map((p) => Buffer.from(p, "base64url"));
  let client;
  try { client = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(clientDataJSON)); } catch { return "the passkey's client data is not JSON"; }
  if (client?.type !== "webauthn.get") return "the passkey's client data is not an assertion";
  if (client.challenge !== sha256(canonical).toString("base64url")) return "the passkey signed another request";
  const origins = jwk.webauthn.origins ?? [];
  if (origins.length && !origins.includes(client.origin)) return `the passkey signed on ${client.origin}, which the key does not allow`;
  if (authData.length < 37 || !authData.subarray(0, 32).equals(sha256(jwk.webauthn.rp_id))) return "the passkey signed for another relying party";
  if ((authData[32] & 0x05) !== 0x05) return "the passkey signed without verifying the person";
  try {
    const key = createPublicKey({ key: { kty: "EC", crv: "P-256", x: jwk.x, y: jwk.y }, format: "jwk" });
    return cryptoVerify("sha256", Buffer.concat([authData, sha256(clientDataJSON)]), { key, dsaEncoding: "der" }, signature) ? null : "the passkey's signature does not verify";
  } catch {
    return "the passkey's signature does not verify";
  }
}
