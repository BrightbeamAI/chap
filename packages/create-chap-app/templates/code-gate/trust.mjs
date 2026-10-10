// Write the trust policy a repository verifies against: `node trust.mjs [options]`.
//
//   --out <file>          where to write it (default: chap-trust.json here)
//   --reviewer <uri>      a reviewer whose approvals count (repeat); default: the humans in chap.config.json
//   --agent <uri>         an agent that may commit (repeat); default: every agent member with a key
//   --people <file>       an allowed_signers file of people whose own signed commits pass with --allow-people
//
// Reads the workspace from the running gate and writes, for each reviewer,
// the name and email chap.config.json gives and the public keys the
// workspace records, for each agent its keys, and the review rule from
// chap.config.json. A reviewed commit names its reviewers by that name and
// email, and verify.mjs holds it to them. Commit the file at the root of the governed
// repository, as chap-trust.json, through a change people review: it is
// what verify.mjs and the pre-commit hook trust, so a key in it is a key
// whose approvals count. Before committing it, confirm each fingerprint it
// prints with the person or agent it names, out of band: whoever joined the
// workspace first under a URI holds the key recorded for it, unless the
// deployment ties joins to a login.

import { readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { agentClient, fingerprint, identityOf, loadGate, reviewRule, TRUST_FILE } from "./lib/gate.mjs";

const here = dirname(fileURLToPath(import.meta.url));
export { fingerprint };

export function parseArgs(argv) {
  const out = { out: join(here, TRUST_FILE), reviewers: [], agents: [], people: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--out") out.out = argv[++i];
    else if (a === "--reviewer") out.reviewers.push(argv[++i]);
    else if (a === "--agent") out.agents.push(argv[++i]);
    else if (a === "--people") out.people = argv[++i];
    else throw new Error(`Unknown option ${a}`);
  }
  return out;
}

/** The trust policy for a gate's workspace as it stands. */
export async function buildTrust(gate, { reviewers = [], agents = [], people = [], keyPath } = {}) {
  const client = await agentClient(gate, { keyPath });
  const described = await client.call("workspace.describe", {});
  const members = new Map((described.members ?? []).map((m) => [m.uri, m]));
  const keysOf = (m) => (m?.jwks?.keys ?? []).filter((k) => k?.kty === "OKP" && typeof k.x === "string");
  const wantedReviewers = reviewers.length ? reviewers : (gate.config.humans ?? []).map((h) => h.uri);
  const out = { chap_trust: 1, workspace: gate.config.workspace, rule: reviewRule(gate).rule, reviewers: {}, agents: {}, people };
  const missing = [];
  const configured = new Map((gate.config.humans ?? []).map((h) => [h.uri, h]));
  for (const uri of wantedReviewers) {
    const m = members.get(uri);
    if (!m || m.type !== "human" || !keysOf(m).length) { missing.push(uri); continue; }
    const id = identityOf(configured.get(uri) ?? { display_name: m.display_name ?? null }, `the reviewer ${uri}`);
    out.reviewers[uri] = { ...(id.name ? { name: id.name } : {}), ...(id.email ? { email: id.email } : {}), keys: keysOf(m) };
  }
  const wantedAgents = agents.length ? agents : [...members.values()].filter((m) => m.type === "agent").map((m) => m.uri);
  for (const uri of wantedAgents) {
    const m = members.get(uri);
    if (m && m.type === "agent" && keysOf(m).length) out.agents[uri] = keysOf(m);
    else missing.push(uri);
  }
  return { trust: out, missing };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  (async () => {
    const args = parseArgs(process.argv.slice(2));
    const gate = await loadGate(here);
    const people = args.people ? (await readFile(args.people, "utf8")).split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#")) : [];
    const { trust, missing } = await buildTrust(gate, { reviewers: args.reviewers, agents: args.agents, people });
    await writeFile(args.out, JSON.stringify(trust, null, 2) + "\n");
    console.log(`Wrote ${args.out}: rule ${trust.rule}.`);
    for (const [kind, set] of [["reviewer", trust.reviewers], ["agent", trust.agents]]) {
      for (const [uri, entry] of Object.entries(set)) {
        const keys = Array.isArray(entry) ? entry : entry.keys;
        const who = !Array.isArray(entry) && entry.name ? `  ${entry.name}${entry.email ? ` <${entry.email}>` : ""}` : "";
        for (const k of keys) console.log(`  ${kind.padEnd(8)} ${uri}${who}  ${k.kid ?? "(no kid)"}  ${fingerprint(k)}`);
        if (kind === "reviewer" && !Array.isArray(entry) && !entry.email) console.log(`  ${uri} has no email in chap.config.json, so a commit it reviews names it without one`);
      }
    }
    if (trust.people.length) console.log(`  ${trust.people.length} people from ${args.people}`);
    for (const uri of missing) console.log(`  not included: ${uri} has not joined the workspace with a key`);
    console.log("Confirm each fingerprint with its holder, then commit the file at the root of the repository as chap-trust.json.");
  })().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}
