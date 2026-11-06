// The agent's signing key.
//
// Generates an Ed25519 keypair with node:crypto and writes the private key
// as a JWK to keys/<agent-uri-slug>.jwk.json, readable by its owner only.
// The public JWK is printed: the agent registers it at participant.join and
// signs every later call with the private half. An existing key file is
// never overwritten; rotate with participant.rotate_key, or remove the
// file and join under a new URI.
//
// A new key's kid is its RFC 7638 thumbprint, as the desk names its keys,
// so no two keys share one. A key file written before keeps the kid it was
// registered under: the kid is read from the file. `chap-server keys` writes
// the key for the agent the project's chap.config.json names, or for
// `--uri <uri>`.
//
// The same key, written in OpenSSH's format beside the JWK, signs git
// commits (git's SSH signing, gpg.format=ssh): `sshKeyFiles` writes
// keys/<slug>.ssh and keys/<slug>.ssh.pub from the JWK, and
// keys/allowed_signers names the public key against the URI, which is
// what `git verify-commit` reads. The public line is what a code host
// takes as a signing key for the agent's account.

import { generateKeyPairSync, createHash, randomBytes } from "node:crypto";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";

/** The kid key files written before 0.3.5 carry: the first sixteen hex characters of the SHA-256 of the URI. */
export function kidFor(uri) {
  return createHash("sha256").update(uri).digest("hex").slice(0, 16);
}

/** The RFC 7638 thumbprint of an Ed25519 JWK: the SHA-256 of {"crv","kty","x"} in that order, base64url. */
export function thumbprint(jwk) {
  return createHash("sha256").update(JSON.stringify({ crv: jwk.crv, kty: jwk.kty, x: jwk.x })).digest("base64url");
}

export function slug(uri) {
  return uri.replace(/[^A-Za-z0-9._-]+/g, "_");
}

export function keyPathFor(uri, dir = resolve("keys")) {
  return join(dir, `${slug(uri)}.jwk.json`);
}

/** The public half of a private JWK, with the kid and the fields the coordinator reads. */
export function publicJwkOf(privateJwk) {
  const { d, ...rest } = privateJwk;
  return { kty: "OKP", crv: "Ed25519", kid: rest.kid, use: "sig", alg: "EdDSA", x: rest.x };
}

/**
 * Generate the key for `uri` into `dir`. Returns { path, publicJwk, created }.
 * With a file already there, nothing is written and created is false.
 */
export async function generateKeyFile(uri, dir = resolve("keys")) {
  const path = keyPathFor(uri, dir);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const { privateKey } = generateKeyPairSync("ed25519");
  const jwk = privateKey.export({ format: "jwk" });
  const privateJwk = { ...jwk, kid: thumbprint(jwk), use: "sig", alg: "EdDSA" };
  try {
    await writeFile(path, JSON.stringify(privateJwk, null, 2) + "\n", { mode: 0o600, flag: "wx" });
  } catch (err) {
    if (err && err.code === "EEXIST") {
      const existing = JSON.parse(await readFile(path, "utf8"));
      return { path, publicJwk: publicJwkOf(existing), created: false };
    }
    throw err;
  }
  return { path, publicJwk: publicJwkOf(privateJwk), created: true };
}

// -- OpenSSH, for signing git commits ----------------------------------------------

const b64u = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");
const u32 = (n) => { const b = Buffer.alloc(4); b.writeUInt32BE(n); return b; };
const sshString = (buf) => Buffer.concat([u32(buf.length), Buffer.isBuffer(buf) ? buf : Buffer.from(buf)]);

/** The ssh-ed25519 public key blob for a JWK. */
function publicBlob(jwk) {
  return Buffer.concat([sshString("ssh-ed25519"), sshString(b64u(jwk.x))]);
}

/** The public key line OpenSSH writes: `ssh-ed25519 <base64> <comment>`. */
export function opensshPublicKey(jwk, comment = "") {
  return `ssh-ed25519 ${publicBlob(jwk).toString("base64")}${comment ? ` ${comment}` : ""}`;
}

/** The private key in OpenSSH's own format (openssh-key-v1, unencrypted), from a private JWK. */
export function opensshPrivateKey(jwk, comment = "") {
  if (jwk.kty !== "OKP" || jwk.crv !== "Ed25519" || typeof jwk.d !== "string") throw new Error("an Ed25519 private JWK is needed");
  const seed = b64u(jwk.d);
  const pub = b64u(jwk.x);
  const check = randomBytes(4);
  let priv = Buffer.concat([check, check, sshString("ssh-ed25519"), sshString(pub), sshString(Buffer.concat([seed, pub])), sshString(comment)]);
  const pad = [];
  for (let i = 1; (priv.length + pad.length) % 8 !== 0; i++) pad.push(i);
  priv = Buffer.concat([priv, Buffer.from(pad)]);
  const body = Buffer.concat([
    Buffer.from("openssh-key-v1\0"), sshString("none"), sshString("none"), sshString(""), u32(1),
    sshString(publicBlob(jwk)), sshString(priv),
  ]).toString("base64");
  return `-----BEGIN OPENSSH PRIVATE KEY-----\n${body.match(/.{1,70}/g).join("\n")}\n-----END OPENSSH PRIVATE KEY-----\n`;
}

/**
 * Write the OpenSSH form of `uri`'s key beside its JWK, and its line in
 * keys/allowed_signers. Returns { privatePath, publicPath, allowedSigners, publicLine }.
 * The private file is readable by its owner only, as ssh-keygen requires.
 */
export async function sshKeyFiles(uri, jwkPath = keyPathFor(uri)) {
  const jwk = await readKeyFile(uri, jwkPath);
  const base = jwkPath.replace(/\.jwk\.json$/, "");
  const privatePath = `${base}.ssh`;
  const publicPath = `${base}.ssh.pub`;
  const publicLine = opensshPublicKey(jwk, uri);
  await writeFile(privatePath, opensshPrivateKey(jwk, uri), { mode: 0o600 });
  await chmod(privatePath, 0o600);
  await writeFile(publicPath, publicLine + "\n");
  const allowedSigners = join(dirname(jwkPath), "allowed_signers");
  let lines = [];
  try { lines = (await readFile(allowedSigners, "utf8")).split("\n").filter((l) => l.trim() && !l.startsWith(`${uri} `)); } catch { /* new file */ }
  lines.push(`${uri} namespaces="git" ${publicLine.split(" ").slice(0, 2).join(" ")}`);
  await writeFile(allowedSigners, lines.join("\n") + "\n");
  return { privatePath, publicPath, allowedSigners, publicLine };
}

/** Read the private JWK for `uri`, with a message that says what to run when it is missing. */
export async function readKeyFile(uri, path = process.env.CHAP_AGENT_KEY ?? keyPathFor(uri)) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (err) {
    if (err && err.code === "ENOENT") throw new Error(`No key for ${uri} at ${path}. Run: npm run keys`);
    throw err;
  }
}
