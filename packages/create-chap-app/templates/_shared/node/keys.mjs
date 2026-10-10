// The agent's signing key.
//
// Generates an Ed25519 keypair with node:crypto and writes the private key
// as a JWK to keys/<agent-uri-slug>.jwk.json, readable by its owner only.
// The public JWK is printed: the agent registers it at participant.join and
// signs every later call with the private half. An existing key file is
// never overwritten; rotate with participant.rotate_key, or remove the
// file and join under a new URI.
//
// The kid is the first sixteen hex characters of the SHA-256 of the URI,
// the same rule the desk's client uses, so a key is recognisable in the
// member's key list. Run it with `npm run keys`, or `node keys.mjs --uri <uri>`.
//
// The same key, written in OpenSSH's format beside the JWK, signs git
// commits (git's SSH signing, gpg.format=ssh): `sshKeyFiles` writes
// keys/<slug>.ssh and keys/<slug>.ssh.pub from the JWK, and
// keys/allowed_signers names the public key against the URI, which is
// what `git verify-commit` reads. The public line is what a code host
// takes as a signing key for the agent's account.

import { generateKeyPairSync, createHash, randomBytes } from "node:crypto";
import { chmod, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

export function kidFor(uri) {
  return createHash("sha256").update(uri).digest("hex").slice(0, 16);
}

export function slug(uri) {
  return uri.replace(/[^A-Za-z0-9._-]+/g, "_");
}

export function keyPathFor(uri, dir = join(here, "keys")) {
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
export async function generateKeyFile(uri, dir = join(here, "keys")) {
  const path = keyPathFor(uri, dir);
  await mkdir(dir, { recursive: true, mode: 0o700 });
  const { privateKey } = generateKeyPairSync("ed25519");
  const privateJwk = { ...privateKey.export({ format: "jwk" }), kid: kidFor(uri), use: "sig", alg: "EdDSA" };
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

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const argv = process.argv.slice(2);
  const flag = argv.indexOf("--uri");
  let uri = flag >= 0 ? argv[flag + 1] : process.env.CHAP_AGENT_URI;
  if (!uri) {
    const config = JSON.parse(await readFile(join(here, "chap.config.json"), "utf8"));
    uri = config.agent?.uri;
  }
  if (!uri) {
    console.error("No agent URI: set agent.uri in chap.config.json or pass --uri");
    process.exit(2);
  }
  const { path, publicJwk, created } = await generateKeyFile(uri);
  console.log(created ? `Key for ${uri} written to ${path} (mode 0600)` : `Key for ${uri} already at ${path}; left as it is`);
  console.log(`Public JWK, registered at participant.join:`);
  console.log(JSON.stringify(publicJwk, null, 2));
  const ssh = await sshKeyFiles(uri, path);
  console.log(`The same key for signing git commits: ${ssh.privatePath} and ${ssh.publicPath}; ${ssh.allowedSigners} names it.`);
  console.log(ssh.publicLine);
}
