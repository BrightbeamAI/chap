/**
 * @brightbeamai/chap-coordinator/crypto
 *
 * Ed25519 signing/verification for the security-signed/1.0 profile, and
 * verification of WebAuthn assertions, the signatures a browser's
 * authenticator makes with a P-256 key.
 *
 * Uses Node's built-in crypto (no external dependencies). Keys are
 * represented as RFC 7517 JWKs.
 */

import {
  createPrivateKey,
  createPublicKey,
  KeyObject,
  sign as nodeSign,
  verify as nodeVerify,
  randomBytes,
  createHash,
} from "node:crypto";
import { canonicalize } from "./canonical.js";

const ED25519_OID = Buffer.from("302a300506032b6570032100", "hex");

function b64urlNoPad(buf: Buffer): string {
  return buf.toString("base64").replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function b64urlDecode(s: string): Buffer {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  return Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/") + pad, "base64");
}

export interface Jwk {
  kty: "OKP";
  crv: "Ed25519";
  kid: string;
  x:   string;
  d?:  string;
  use?: string;
  alg?: string;
}

/**
 * Deterministically derive a 32-byte Ed25519 seed from a participant URI.
 * Demo / test only. production deployments supply real keys.
 */
export function deriveSeed(uri: string): Buffer {
  return createHash("sha256").update("chap:" + uri).digest();
}

/**
 * Build a Node KeyObject (private key) from a raw 32-byte Ed25519 seed.
 */
export function privateKeyFromSeed(seed: Buffer): KeyObject {
  if (seed.length !== 32) {
    throw new Error("Ed25519 seed must be 32 bytes");
  }
  // PKCS#8 DER for Ed25519: SEQ(SEQ(OID), OCTET(OCTET(seed)))
  const pkcs8 = Buffer.concat([
    Buffer.from("302e020100300506032b657004220420", "hex"),
    seed,
  ]);
  return createPrivateKey({ key: pkcs8, format: "der", type: "pkcs8" });
}

/** Public key bytes (32 bytes) from a private key. */
export function publicKeyBytes(privateKey: KeyObject): Buffer {
  const pub = createPublicKey(privateKey);
  const der = pub.export({ format: "der", type: "spki" }) as Buffer;
  // SPKI for Ed25519 is the OID prefix + 32-byte raw public key
  return der.subarray(der.length - 32);
}

/** Public KeyObject from raw 32-byte Ed25519 public key. */
export function publicKeyFromRaw(raw: Buffer): KeyObject {
  if (raw.length !== 32) {
    throw new Error("Ed25519 public key must be 32 bytes");
  }
  const der = Buffer.concat([ED25519_OID, raw]);
  return createPublicKey({ key: der, format: "der", type: "spki" });
}

/** Render a JWK from a Node private KeyObject. */
export function jwkFromPrivateKey(uri: string, key: KeyObject): Jwk {
  const pub = publicKeyBytes(key);
  return {
    kty: "OKP",
    crv: "Ed25519",
    kid: createHash("sha256").update(uri).digest("hex").slice(0, 16),
    use: "sig",
    alg: "EdDSA",
    x:   b64urlNoPad(pub),
  };
}

/** Sign canonical bytes; return ``ed25519:<kid>:<base64>``. */
export function signEnvelope(canonical: Buffer, privateKey: KeyObject, kid: string): string {
  const sig = nodeSign(null, canonical, privateKey);
  return `ed25519:${kid}:${sig.toString("base64")}`;
}

/** Verify a CHAP sig string against canonical bytes and a public key. */
export function verifyEnvelope(canonical: Buffer, sigTag: string, publicKey: KeyObject): boolean {
  if (!sigTag.startsWith("ed25519:")) return false;
  const parts = sigTag.split(":");
  if (parts.length < 2) return false;
  const sigB64 = parts[parts.length - 1];
  try {
    const sig = Buffer.from(sigB64, "base64");
    return nodeVerify(null, canonical, publicKey, sig);
  } catch {
    return false;
  }
}

/** Parse a JWK x (base64url) into a Node public KeyObject. */
export function publicKeyFromJwk(jwk: Jwk): KeyObject {
  return publicKeyFromRaw(b64urlDecode(jwk.x));
}

/**
 * The RFC 7638 thumbprint of a public JWK: SHA-256 over the JSON of its
 * required members in lexicographic order, base64url without padding.
 * The members are {crv, kty, x} for an OKP key and {crv, kty, x, y} for an
 * EC key, and for any other key, or one whose members are not strings,
 * there is none.
 */
export function jwkThumbprint(jwk: unknown): string | undefined {
  if (jwk === null || typeof jwk !== "object" || Array.isArray(jwk)) return undefined;
  const key = jwk as Record<string, unknown>;
  const names = key.kty === "OKP" ? ["crv", "kty", "x"] : key.kty === "EC" ? ["crv", "kty", "x", "y"] : undefined;
  if (!names || names.some((n) => typeof key[n] !== "string")) return undefined;
  const members = Object.fromEntries(names.map((n) => [n, key[n]]));
  return b64urlNoPad(createHash("sha256").update(canonicalize(members)).digest());
}

/** A WebAuthn assertion, as the last part of a `webauthn:` tag carries it. */
export interface WebAuthnAssertion {
  authenticatorData: Buffer;
  clientDataJSON:    Buffer;
  signature:         Buffer;
}

// Unpadded base64url. A length of 1 more than a multiple of 4 encodes no
// whole byte, and is refused here, since Node and Python decode it apart.
const B64URL = /^[A-Za-z0-9_-]+$/;

/**
 * The assertion in `<authenticatorData>.<clientDataJSON>.<signature>`, each
 * part unpadded base64url, or undefined where the part is not that.
 */
export function parseWebAuthnAssertion(part: string): WebAuthnAssertion | undefined {
  const pieces = part.split(".");
  if (pieces.length !== 3 || pieces.some((p) => !B64URL.test(p) || p.length % 4 === 1)) return undefined;
  const [authenticatorData, clientDataJSON, signature] = pieces.map((p) => Buffer.from(p, "base64url"));
  return { authenticatorData, clientDataJSON, signature };
}

// A P-256 coordinate: 32 bytes, unpadded base64url.
const COORDINATE = /^[A-Za-z0-9_-]{43}$/;

/**
 * Whether a JWK is a WebAuthn key: a P-256 key for ES256 with a `webauthn`
 * member naming its `rp_id`, and, where it limits them, its `origins`.
 */
export function isWebAuthnKey(jwk: unknown): boolean {
  if (jwk === null || typeof jwk !== "object" || Array.isArray(jwk)) return false;
  const key = jwk as Record<string, unknown>;
  const w = key.webauthn as Record<string, unknown> | null | undefined;
  return key.kty === "EC" && key.crv === "P-256"
    && typeof key.x === "string" && COORDINATE.test(key.x) && typeof key.y === "string" && COORDINATE.test(key.y)
    && (key.alg === undefined || key.alg === "ES256")
    && w !== null && typeof w === "object" && !Array.isArray(w)
    && typeof w.rp_id === "string" && w.rp_id !== ""
    && (w.origins === undefined || (Array.isArray(w.origins) && w.origins.every((o) => typeof o === "string")));
}

const UTF8 = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

/**
 * Why a WebAuthn assertion does not sign a request, or null when it does.
 * `canonical` is the JCS form of the request without its `sig`, and `jwk`
 * a key isWebAuthnKey accepts. The checks are the relying party's in the
 * WebAuthn assertion procedure: the client data says it is an assertion,
 * over this request, from an origin the key allows; the authenticator data
 * is for the key's relying party, with the user present and verified; and
 * the ES256 signature covers the authenticator data and the hash of the
 * client data.
 */
export function webAuthnFailure(canonical: Buffer, assertion: WebAuthnAssertion,
                                jwk: Record<string, unknown>): string | null {
  let client: unknown;
  try { client = JSON.parse(UTF8.decode(assertion.clientDataJSON)); } catch { client = undefined; }
  if (client === null || typeof client !== "object" || Array.isArray(client)) {
    return "WebAuthn clientDataJSON is not a JSON object";
  }
  const data = client as Record<string, unknown>;
  if (data.type !== "webauthn.get") return "WebAuthn clientDataJSON type is not webauthn.get";
  if (data.challenge !== b64urlNoPad(createHash("sha256").update(canonical).digest())) {
    return "WebAuthn challenge is not the digest of this request";
  }
  const w = jwk.webauthn as { rp_id: string; origins?: string[] };
  if (w.origins !== undefined && w.origins.length > 0 && !w.origins.includes(data.origin as string)) {
    return "WebAuthn origin is not one the key allows";
  }
  const auth = assertion.authenticatorData;
  if (auth.length < 37) return "WebAuthn authenticatorData is too short";
  if (!auth.subarray(0, 32).equals(createHash("sha256").update(w.rp_id, "utf-8").digest())) {
    return "WebAuthn rpIdHash does not match the key's rp_id";
  }
  // UP, user present, is bit 0 and UV, user verified, bit 2.
  if ((auth[32] & 0x05) !== 0x05) return "WebAuthn assertion lacks user presence or verification";
  try {
    const key = createPublicKey({ key: { kty: "EC", crv: "P-256", x: jwk.x as string, y: jwk.y as string },
                                  format: "jwk" });
    const signed = Buffer.concat([auth, createHash("sha256").update(assertion.clientDataJSON).digest()]);
    return nodeVerify("sha256", signed, { key, dsaEncoding: "der" }, assertion.signature)
      ? null : "Signature failed verification";
  } catch {
    return "Signature failed verification";
  }
}

/** Demo helper: build a deterministic keypair + JWK for a URI. */
export function deriveKeypair(uri: string): { privateKey: KeyObject; jwk: Jwk } {
  const seed = deriveSeed(uri);
  const privateKey = privateKeyFromSeed(seed);
  return { privateKey, jwk: jwkFromPrivateKey(uri, privateKey) };
}

export { b64urlNoPad, b64urlDecode };
