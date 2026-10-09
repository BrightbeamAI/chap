// The desk's CHAP client. Plain ES module with no dependencies, so the same
// file runs in the browser and under `node --test`.
//
// It does four things: send a JSON-RPC call to POST /chap, canonicalise an
// envelope the way both coordinators do (RFC 8785 JCS over I-JSON), sign an
// envelope with an Ed25519 key held in WebCrypto, and compute an RFC 6902
// patch between two JSON values for decide.override.

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
const fromB64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const hex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");

// -- keys -------------------------------------------------------------------
//
// A signer holds one Ed25519 key for one participant. `kid` follows the
// coordinators' demo helper, the first sixteen hex characters of the SHA-256
// of the URI, so a key is recognisable in the member's key list. Any kid
// works; the coordinator looks the key up by the kid the signature names.

export async function generateSigner(uri) {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const privateJwk = await crypto.subtle.exportKey("jwk", pair.privateKey);
  return signerFromJwk(uri, privateJwk);
}

export async function signerFromJwk(uri, privateJwk) {
  const privateKey = await crypto.subtle.importKey("jwk", privateJwk, { name: "Ed25519" }, true, ["sign"]);
  const kidBytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(uri));
  const kid = hex(kidBytes).slice(0, 16);
  const publicJwk = { kty: "OKP", crv: "Ed25519", kid, use: "sig", alg: "EdDSA", x: privateJwk.x };
  return {
    uri,
    kid,
    publicJwk,
    privateJwk,
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
 * coordinator with requireSignatures expects.
 */
export function makeClient({ url = "/chap", workspace, from, signer = null, fetchImpl = globalThis.fetch }) {
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
    const res = await fetchImpl(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(envelope),
    });
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
