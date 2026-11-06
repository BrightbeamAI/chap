// Where the desk keeps what a reviewer signs with, in this browser's
// IndexedDB for the desk's address, one record per workspace and reviewer:
//
//   - a passkey: the credential's id and its public JWK. The private key is
//     in the authenticator, which signs only once the person is verified.
//   - a browser key: a WebCrypto Ed25519 key that cannot be exported, and
//     its public JWK. A page on the address can ask the browser to sign with
//     it, and no script can read the key out to sign somewhere else.
//
// Before 0.3.5 the desk kept the key as a JWK in localStorage, where any
// script on the address could copy it. A key found there is moved: imported
// as a key that cannot be exported, under the kid it was registered with,
// and removed from localStorage.

import { generateSigner, legacyKid, passkeySigner, signerFromKey, webauthnJwk } from "./chap-client.mjs";

const DB = "chap-desk";
const STORE = "keys";

function open() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function request(mode, run) {
  const db = await open();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = run(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

const recordKey = (workspace, uri) => `${workspace} ${uri}`;
const legacyName = (workspace, uri) => `chap-desk-key:${workspace}:${uri}`;

/** The signer the browser holds for `uri` in `workspace`, a passkey or a browser key, or null. */
export async function storedSigner(workspace, uri) {
  const record = await request("readonly", (store) => store.get(recordKey(workspace, uri)));
  if (!record) return null;
  if (record.credentialId) return passkeySigner({ uri, credentialId: record.credentialId, publicJwk: record.publicJwk });
  return signerFromKey({ uri, privateKey: record.privateKey, publicJwk: record.publicJwk });
}

/** Keep `signer` as what the browser signs with for `uri` in `workspace`; whatever it held before goes. */
export async function keepSigner(workspace, uri, signer) {
  const record = signer.scheme === "webauthn"
    ? { credentialId: signer.credentialId, publicJwk: signer.publicJwk }
    : { privateKey: signer.privateKey, publicJwk: signer.publicJwk };
  await request("readwrite", (store) => store.put(record, recordKey(workspace, uri)));
  return signer;
}

/**
 * A browser key for `uri` in `workspace`: the one an earlier desk left in
 * `storage` (moved here), or a new one. Returns { signer, moved }, where
 * `moved` says an earlier desk's key was moved.
 */
export async function browserKey(workspace, uri, storage = localStorage) {
  const saved = storage.getItem(legacyName(workspace, uri));
  if (saved) {
    const jwk = JSON.parse(saved);
    const privateKey = await crypto.subtle.importKey("jwk", { kty: "OKP", crv: "Ed25519", x: jwk.x, d: jwk.d }, { name: "Ed25519" }, false, ["sign"]);
    const publicJwk = { kty: "OKP", crv: "Ed25519", kid: await legacyKid(uri), use: "sig", alg: "EdDSA", x: jwk.x };
    const signer = await keepSigner(workspace, uri, signerFromKey({ uri, privateKey, publicJwk }));
    storage.removeItem(legacyName(workspace, uri));
    return { signer, moved: true };
  }
  return { signer: await keepSigner(workspace, uri, await generateSigner(uri)), moved: false };
}

/**
 * Make a passkey for `uri` in `workspace`, and give its signer; keepSigner
 * keeps it. The relying party is the desk's host and the one origin its
 * assertions may come from the desk's own; the person is verified, by a
 * fingerprint, a face or a PIN, now and on each signature. `platform` asks
 * for the authenticator built into this device.
 */
export async function makePasskey(workspace, uri, { displayName = null, platform = false } = {}) {
  const userId = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(recordKey(workspace, uri))));
  const credential = await navigator.credentials.create({ publicKey: {
    rp: { id: location.hostname, name: "CHAP review desk" },
    user: { id: userId, name: uri, displayName: displayName || uri },
    challenge: crypto.getRandomValues(new Uint8Array(32)),
    pubKeyCredParams: [{ type: "public-key", alg: -7 }],
    authenticatorSelection: { ...(platform ? { authenticatorAttachment: "platform" } : {}), userVerification: "required", residentKey: "discouraged" },
    attestation: "none",
    timeout: 120_000,
  } });
  if (credential.response.getPublicKeyAlgorithm() !== -7) throw new Error("The authenticator made a key the coordinator cannot check; a passkey here signs with ES256.");
  const spki = await crypto.subtle.importKey("spki", credential.response.getPublicKey(), { name: "ECDSA", namedCurve: "P-256" }, true, ["verify"]);
  const { x, y } = await crypto.subtle.exportKey("jwk", spki);
  const publicJwk = await webauthnJwk({ x, y, rpId: location.hostname, origin: location.origin });
  return passkeySigner({ uri, credentialId: credential.rawId, publicJwk });
}
