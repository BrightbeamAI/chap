// The desk's CHAP client. Plain ES module with no dependencies, so the same
// file runs in the browser and under `node --test`.
//
// It does four things: send a JSON-RPC call to POST /chap, canonicalise an
// envelope the way both coordinators do (RFC 8785 JCS over I-JSON), sign an
// envelope, with an Ed25519 key held in WebCrypto or with a passkey through
// WebAuthn, and compute an RFC 6902 patch between two JSON values for
// decide.override.

// -- canonical JSON -----------------------------------------------------------
//
// Mirrors packages/coordinator/src/canonical.ts: keys sorted by UTF-16 code
// units, no whitespace, integers only, strings escaped as JSON.stringify does.

export function canonicalize(value) {
  return canon(value);
}

function canon(value) {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";
  if (typeof value === "string") {
    assertNoLoneSurrogate(value);
    return JSON.stringify(value);
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("Non-finite numbers are not permitted in a CHAP canonical value");
    if (!Number.isInteger(value)) throw new Error('CHAP canonical numbers must be integers; represent decimals as strings (e.g. "8.2")');
    if (Math.abs(value) > Number.MAX_SAFE_INTEGER) throw new Error("CHAP canonical integers must be within the safe-integer range");
    return value.toString();
  }
  if (Array.isArray(value)) return "[" + value.map(canon).join(",") + "]";
  if (typeof value === "object") {
    const keys = Object.keys(value).filter((k) => value[k] !== undefined).sort();
    return "{" + keys.map((k) => JSON.stringify(k) + ":" + canon(value[k])).join(",") + "}";
  }
  throw new Error(`Cannot canonicalise a ${typeof value}`);
}

function assertNoLoneSurrogate(s) {
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      const next = i + 1 < s.length ? s.charCodeAt(i + 1) : 0;
      if (next < 0xdc00 || next > 0xdfff) throw new Error("CHAP canonical strings must be valid Unicode");
      i++;
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      throw new Error("CHAP canonical strings must be valid Unicode");
    }
  }
}

/**
 * The content hash both coordinators compute: "sha256:" and the hex digest
 * of the canonical bytes. A decision that carries it as
 * `approved_artefact_digest` is bound to the artefact the reviewer saw.
 */
export async function contentHash(value) {
  const bytes = new TextEncoder().encode(canonicalize(value));
  return "sha256:" + hex(await crypto.subtle.digest("SHA-256", bytes));
}

// -- base64 -----------------------------------------------------------------

const b64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));
const b64url = (bytes) => b64(bytes).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
const fromB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const fromB64url = (s) => fromB64(s.replace(/-/g, "+").replace(/_/g, "/"));
const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
const sha256 = (bytes) => crypto.subtle.digest("SHA-256", bytes);

// -- keys -------------------------------------------------------------------
//
// A signer holds one Ed25519 key for one participant. A new key's `kid` is
// its RFC 7638 thumbprint, so two keys never share one and a key names
// itself in the member's key list. A key made before keeps the kid it was
// registered under; the coordinator looks a key up by the kid the signature
// names. The private key is a WebCrypto key that cannot be exported: a
// script that gets hold of it can sign with it in this page, and cannot copy
// it out.

/** The RFC 7638 thumbprint of a public JWK, base64url without padding: the SHA-256 of its required members, in order. */
export async function thumbprint(jwk) {
  const members = jwk.kty === "EC" ? { crv: jwk.crv, kty: jwk.kty, x: jwk.x, y: jwk.y } : { crv: jwk.crv, kty: jwk.kty, x: jwk.x };
  return b64url(await sha256(new TextEncoder().encode(JSON.stringify(members))));
}

/**
 * A key's fingerprint as the code gate's trust command prints it: SHA-256
 * of the raw public key, base64. An Ed25519 key's raw form is its 32 bytes;
 * a P-256 key's is the uncompressed point, 0x04 then x and y.
 */
export async function fingerprint(jwk) {
  const raw = jwk.kty === "EC" ? Uint8Array.from([4, ...fromB64url(jwk.x), ...fromB64url(jwk.y)]) : fromB64url(jwk.x);
  return "SHA256:" + b64(await sha256(raw)).replace(/=+$/, "");
}

/** The kid the desk gave a key before 0.3.5: the first sixteen hex characters of the SHA-256 of the URI. */
export async function legacyKid(uri) {
  return hex(await sha256(new TextEncoder().encode(uri))).slice(0, 16);
}

/** The public JWK the coordinator registers, with the fields it reads. */
const publicJwkFor = (x, kid) => ({ kty: "OKP", crv: "Ed25519", kid, use: "sig", alg: "EdDSA", x });

/** A new key for `uri`, which cannot be exported, named by its thumbprint. */
export async function generateSigner(uri) {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
  const { x } = await crypto.subtle.exportKey("jwk", pair.publicKey);
  const publicJwk = publicJwkFor(x, await thumbprint({ kty: "OKP", crv: "Ed25519", x }));
  return signerFromKey({ uri, privateKey: pair.privateKey, publicJwk });
}

/** A signer for a private JWK, such as an agent's key file. Its kid is the one the file gives, else the key's thumbprint. */
export async function signerFromJwk(uri, privateJwk) {
  const privateKey = await crypto.subtle.importKey("jwk", privateJwk, { name: "Ed25519" }, false, ["sign"]);
  const kid = typeof privateJwk.kid === "string" ? privateJwk.kid : await thumbprint(privateJwk);
  return signerFromKey({ uri, privateKey, publicJwk: publicJwkFor(privateJwk.x, kid) });
}

/** A signer for a WebCrypto private key and its public JWK, which carries the kid. */
export function signerFromKey({ uri, privateKey, publicJwk }) {
  const kid = publicJwk.kid;
  return {
    scheme: "ed25519",
    uri,
    kid,
    publicJwk,
    privateKey,
    /** Returns the envelope with its `sig` set. The input is not changed. */
    async sign(envelope) {
      const unsigned = { ...envelope };
      delete unsigned.sig;
      const bytes = new TextEncoder().encode(canonicalize(unsigned));
      const sig = await crypto.subtle.sign({ name: "Ed25519" }, privateKey, bytes);
      return { ...unsigned, sig: `ed25519:${kid}:${b64(sig)}` };
    },
  };
}

// -- passkeys -----------------------------------------------------------------
//
// A passkey's private key never leaves the authenticator, which signs only
// what WebAuthn asks of it, and only once the person is verified, with a
// fingerprint, a face or a PIN. An envelope is signed by asking for an
// assertion whose challenge is the SHA-256 of the envelope's canonical form
// without its `sig`; the tag carries the assertion whole, and the
// coordinator checks it as a relying party does (security-signed/1.0 3):
//
//   webauthn:<kid>:<authenticatorData>.<clientDataJSON>.<signature>
//
// The key is registered at participant.join as a P-256 JWK naming the
// relying party, the desk's host, and the one origin assertions may come
// from, the desk's own.

/** The JWK the coordinator registers for a passkey's P-256 public key, named by its RFC 7638 thumbprint. */
export async function webauthnJwk({ x, y, rpId, origin }) {
  const kid = await thumbprint({ kty: "EC", crv: "P-256", x, y });
  return { kty: "EC", crv: "P-256", x, y, alg: "ES256", kid, use: "sig", webauthn: { rp_id: rpId, origins: [origin] } };
}

/**
 * A signer for a passkey: the credential's id and its public JWK. Each
 * signature is an assertion `credentials` makes, navigator.credentials by
 * default, with user verification required, so each one asks the person.
 */
export function passkeySigner({ uri, credentialId, publicJwk, credentials = globalThis.navigator?.credentials }) {
  const kid = publicJwk.kid;
  return {
    scheme: "webauthn",
    uri,
    kid,
    publicJwk,
    credentialId,
    /** Returns the envelope with its `sig` set. The input is not changed. */
    async sign(envelope) {
      const unsigned = { ...envelope };
      delete unsigned.sig;
      const challenge = new Uint8Array(await sha256(new TextEncoder().encode(canonicalize(unsigned))));
      const assertion = await credentials.get({ publicKey: {
        challenge, rpId: publicJwk.webauthn.rp_id, userVerification: "required", timeout: 120_000,
        allowCredentials: [{ type: "public-key", id: credentialId }],
      } });
      const { authenticatorData, clientDataJSON, signature } = assertion.response;
      return { ...unsigned, sig: `webauthn:${kid}:${b64url(authenticatorData)}.${b64url(clientDataJSON)}.${b64url(signature)}` };
    },
  };
}

export async function verifyWithJwk(publicJwk, envelope) {
  const key = await crypto.subtle.importKey("jwk", { kty: "OKP", crv: "Ed25519", x: publicJwk.x }, { name: "Ed25519" }, true, ["verify"]);
  const { sig, ...rest } = envelope;
  const parts = (sig ?? "").split(":");
  if (parts.length !== 3) return false;
  return crypto.subtle.verify({ name: "Ed25519" }, key, fromB64(parts[2]), new TextEncoder().encode(canonicalize(rest)));
}

// -- the client -------------------------------------------------------------

let nextId = 1;

/**
 * A client for one participant. `signer` is optional; with one, every call
 * except workspace.create and participant.join is signed, which is what a
 * coordinator with requireSignatures expects. `enrolment` is the code a
 * desk server prints at start, or a function that gives it: a person's
 * first join carries it in the x-chap-enrolment header.
 */
export function makeClient({ url = "/chap", workspace, from, signer = null, fetchImpl = globalThis.fetch, enrolment = null }) {
  async function call(method, params = {}) {
    let envelope = {
      jsonrpc: "2.0",
      id: `desk-${Date.now()}-${nextId++}`,
      method,
      params: { workspace, from, ...params },
    };
    if (signer && method !== "workspace.create" && method !== "participant.join") {
      envelope = await signer.sign(envelope);
    }
    const headers = { "content-type": "application/json" };
    const code = method === "participant.join" ? (typeof enrolment === "function" ? enrolment() : enrolment) : null;
    if (code) headers["x-chap-enrolment"] = code;
    const res = await fetchImpl(url, { method: "POST", headers, body: JSON.stringify(envelope) });
    const body = await res.json();
    if (body.error) {
      const err = new Error(`${method}: ${body.error.message} (${body.error.code})`);
      err.code = body.error.code;
      err.data = body.error.data;
      throw err;
    }
    return body.result;
  }
  return { call, workspace, from, signer };
}

/**
 * The Authorization header of a read from the read API, signed with a
 * member's key, where the server takes reads from members alone: a
 * workspace.describe naming the path and the time, which the server checks
 * as the coordinator checks a call. `path` is the path read, with its query.
 */
export async function signedRead(signer, { workspace, from, path }) {
  const envelope = await signer.sign({ jsonrpc: "2.0", id: `read-${Date.now()}-${nextId++}`, method: "workspace.describe",
    params: { workspace, from, path, ts: new Date().toISOString() } });
  const json = JSON.stringify(envelope);
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return `CHAP ${btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")}`;
}

// -- RFC 6902 patch -----------------------------------------------------------
//
// A small diff for decide.override: objects are compared key by key, and
// anything else that differs is replaced whole. The coordinator applies the
// patch to the artefact under review and refuses one that does not apply.

export function jsonPatch(before, after, path = "") {
  if (deepEqual(before, after)) return [];
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  if (isObj(before) && isObj(after)) {
    const ops = [];
    for (const key of Object.keys(before)) {
      if (!(key in after)) ops.push({ op: "remove", path: path + "/" + escapePointer(key) });
    }
    for (const key of Object.keys(after)) {
      const p = path + "/" + escapePointer(key);
      if (!(key in before)) ops.push({ op: "add", path: p, value: after[key] });
      else ops.push(...jsonPatch(before[key], after[key], p));
    }
    return ops;
  }
  return [{ op: "replace", path: path === "" ? "" : path, value: after }];
}

export function escapePointer(token) {
  return token.replace(/~/g, "~0").replace(/\//g, "~1");
}

/** Structural equality of two JSON values. Never throws, unlike canonicalize. */
export function deepEqual(a, b) {
  if (a === b) return true;
  if (a === null || b === null || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((v, i) => deepEqual(v, b[i]));
  const ka = Object.keys(a).filter((k) => a[k] !== undefined);
  const kb = Object.keys(b).filter((k) => b[k] !== undefined);
  return ka.length === kb.length && ka.every((k) => kb.includes(k) && deepEqual(a[k], b[k]));
}
