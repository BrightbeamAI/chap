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

import { generateKeyPairSync, createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
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
  let uri = flag >= 0 ? argv[flag + 1] : undefined;
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
}
