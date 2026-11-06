// The gate's server: @brightbeamai/chap-server for the gate project, with
// the reviewers and agents its trust policy pins joined, with their pinned
// keys, before anything else can join under their names.

import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { createChapServer, loadConfig } from "@brightbeamai/chap-server";
import { policyFromTrust, TRUST_FILE } from "./gate.mjs";

/**
 * The members the gate project's trust policy pins, to join at start: the
 * file `trust` in chap.config.json names (a path from the project), or
 * chap-trust.json in the project when that exists. A `trust` that names no
 * file is an error, so the gate never starts without a policy it was told
 * to keep. Returns { path, members }, with path null when there is none.
 */
export async function trustedMembers(config) {
  const path = resolve(config.dir, config.trust ?? TRUST_FILE);
  let text;
  try { text = await readFile(path, "utf8"); } catch (e) {
    if (e.code === "ENOENT" && config.trust === undefined) return { path: null, members: [] };
    throw new Error(`chap.config.json names the trust policy ${path}, which cannot be read: ${e.message}`);
  }
  const policy = policyFromTrust(JSON.parse(text), path);
  if (policy.workspace && policy.workspace !== config.workspace) throw new Error(`${path} covers ${policy.workspace}, and chap.config.json names ${config.workspace}`);
  return {
    path,
    members: [
      ...Object.entries(policy.reviewers).map(([uri, keys]) => ({ uri, type: "human", role: "reviewer", display_name: policy.identities[uri]?.name ?? undefined, keys })),
      ...Object.entries(policy.agents).map(([uri, keys]) => ({ uri, type: "agent", role: "drafter", keys })),
    ],
  };
}

/** Start the gate for a configuration loadConfig returned. Returns { coord, server, base }. */
export async function startGate(config, { log = console.log } = {}) {
  const trust = await trustedMembers(config);
  const chap = await createChapServer({ config, enrol: trust.members, log });
  const { base } = await chap.listen({ log });
  if (trust.path) log(`  trust    ${trust.path}: ${trust.members.length} member${trust.members.length === 1 ? "" : "s"} joined with their pinned keys`);
  return { coord: chap.coord, server: chap.server, base };
}

/** Serve the gate project in `dir`: chap.config.json there, or the file CHAP_CONFIG names. */
export async function serveGate({ dir }) {
  return startGate(await loadConfig(process.env.CHAP_CONFIG ?? join(dir, "chap.config.json")));
}
