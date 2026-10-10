// The whole gate in one command: `npm run demo`, or `node demo.mjs [--fresh]`.
//
// Makes the agent's key when there is none, starts the gate with its store
// under data/, makes the demo repository under the gate's hooks when there
// is none, and runs the built-in agent on tasks.csv in it, on the branch
// agent/demo. Open the desk at the address printed: the agent waits for a
// reviewer to join, then each change arrives as a diff to decide. Approve,
// request changes, edit or reject, and watch the agent commit, revise or
// take the change back. With python3 and chap-analytics installed, the
// analytics pages are written every minute and the desk's Insights view
// links them.
//
//   --fresh   start again: remove data/, demo-repo/ and analytics/ first (the key stays)
//   --once    stop once every task in tasks.csv is settled
//
// Ctrl-C stops everything. The store, the repository and its commits stay,
// so a second run carries on where the first stopped.

import { spawn, spawnSync } from "node:child_process";
import { access, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig, start } from "./server.mjs";
import { generateKeyFile, sshKeyFiles } from "./keys.mjs";
import { createDemoRepo } from "./demo-repo.mjs";
import { run as runAgent } from "./agent.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const exists = (p) => access(p).then(() => true, () => false);

export async function demo({ fresh = false, once = false, log = console.log } = {}) {
  if (fresh) {
    for (const d of ["data", "demo-repo", "analytics"]) await rm(join(here, d), { recursive: true, force: true });
    log("Removed data/, demo-repo/ and analytics/.");
  }
  const config = await loadConfig();
  const { path: keyPath, created } = await generateKeyFile(config.agent.uri, join(here, "keys"));
  await sshKeyFiles(config.agent.uri, keyPath);
  if (created) log(`The agent's key is new: ${keyPath}`);

  const { server, base } = await start(config);
  const repo = join(here, "demo-repo");
  if (!(await exists(repo))) await createDemoRepo(repo, { log: (l) => log(`  ${l}`) });

  let analytics = null;
  const python = process.env.PYTHON ?? "python3";
  const probe = spawnSync(python, ["-c", "import chap_analytics"], { stdio: "ignore" });
  if (config.store !== ":memory:" && probe.status === 0) {
    analytics = spawn(python, [join(here, "analytics.py"), "--watch", "60"], { cwd: here, stdio: ["ignore", "pipe", "pipe"] });
    analytics.stdout.on("data", (d) => String(d).split("\n").filter(Boolean).forEach((l) => log(`analytics  ${l}`)));
    analytics.stderr.on("data", (d) => String(d).split("\n").filter(Boolean).forEach((l) => log(`analytics  ${l}`)));
  }

  log("");
  log(`  Open the desk at ${base}/`);
  log(`  The agent works in ${repo} on the branch agent/demo.`);
  log(`  Every change it wants to commit waits for you there as a diff.`);
  log(analytics ? "  chap-analytics writes its pages every minute; the desk's Insights view links them." : "  pip install chap-analytics to have its report and refinement page in the desk's Insights view.");
  log(`  Afterwards: cd demo-repo && git log --show-signature --show-notes=chap agent/demo`);
  log(`             node verify.mjs main..agent/demo --repo demo-repo`);
  log("");

  const stop = () => { analytics?.kill(); server.close(); };
  process.once("SIGINT", () => { log("\nStopped. The store and the repository stay; npm run demo carries on from here."); stop(); process.exit(0); });
  const outcomes = await runAgent({ source: join(here, "tasks.csv"), repo, branch: "agent/demo", once, pollMs: 1500, log: (l) => log(`agent  ${l}`), gateDir: here, keyPath });
  if (once) {
    log(`Every task settled: ${Object.values(outcomes).join(", ")}.`);
    stop();
  }
  return outcomes;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const argv = process.argv.slice(2);
  demo({ fresh: argv.includes("--fresh"), once: argv.includes("--once") }).catch((e) => {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(1);
  });
}
