/**
 * @brightbeamai/coordinator/crypto
 *
 * Ed25519 signing/verification for the security-signed/1.0 profile.
 *
 * Uses Node's built-in crypto (no external dependencies). Keys are
 * represented as RFC 7517 JWKs with kty=OKP, crv=Ed25519.
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

/** Demo helper: build a deterministic keypair + JWK for a URI. */
export function deriveKeypair(uri: string): { privateKey: KeyObject; jwk: Jwk } {
  const seed = deriveSeed(uri);
  const privateKey = privateKeyFromSeed(seed);
  return { privateKey, jwk: jwkFromPrivateKey(uri, privateKey) };
}

export { b64urlNoPad, b64urlDecode };
