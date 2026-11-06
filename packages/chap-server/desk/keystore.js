// Where the desk keeps a reviewer's signing key: in this browser's
// IndexedDB for the desk's address, as a WebCrypto key that cannot be
// exported. A page on the address can ask the browser to sign with it, and
// no script can read the key out to sign somewhere else.
//
// Before 0.3.5 the desk kept the key as a JWK in localStorage, where any
// script on the address could copy it. A key found there is moved: imported
// as a key that cannot be exported, under the kid it was registered with,
// and removed from localStorage.

import { generateSigner, legacyKid, signerFromKey } from "./chap-client.mjs";

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

/** The signer the browser holds for `uri` in `workspace`, or null. */
export async function storedSigner(workspace, uri) {
  const record = await request("readonly", (store) => store.get(recordKey(workspace, uri)));
  return record ? signerFromKey({ uri, privateKey: record.privateKey, publicJwk: record.publicJwk }) : null;
}

async function keep(workspace, uri, signer) {
  await request("readwrite", (store) => store.put({ privateKey: signer.privateKey, publicJwk: signer.publicJwk }, recordKey(workspace, uri)));
  return signer;
}

/**
 * The signer for `uri` in `workspace`: the one this browser holds, the one
 * an earlier desk left in `storage` (moved here), or a new one. Returns
 * { signer, moved }, where `moved` says an earlier desk's key was moved.
 */
export async function signerFor(workspace, uri, storage = localStorage) {
  const held = await storedSigner(workspace, uri);
  if (held) return { signer: held, moved: false };
  const saved = storage.getItem(legacyName(workspace, uri));
  if (saved) {
    const jwk = JSON.parse(saved);
    const privateKey = await crypto.subtle.importKey("jwk", { kty: "OKP", crv: "Ed25519", x: jwk.x, d: jwk.d }, { name: "Ed25519" }, false, ["sign"]);
    const publicJwk = { kty: "OKP", crv: "Ed25519", kid: await legacyKid(uri), use: "sig", alg: "EdDSA", x: jwk.x };
    const signer = await keep(workspace, uri, signerFromKey({ uri, privateKey, publicJwk }));
    storage.removeItem(legacyName(workspace, uri));
    return { signer, moved: true };
  }
  return { signer: await keep(workspace, uri, await generateSigner(uri)), moved: false };
}

