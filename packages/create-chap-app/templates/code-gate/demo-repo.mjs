// A small repository to put under the gate: `npm run demo-repo`, or
// `node demo-repo.mjs [--dir <path>]`.
//
// Writes a tiny Node library with one function, a test and a README, makes
// the first commit, and points the repository's hooks at this gate. The
// sample tasks in tasks.csv are written for it, so the built-in agent can
// run against it with no model and no key.

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { usage, wantsHelp } from "@brightbeamai/chap-server";
import { createDemoRepo } from "@brightbeamai/chap-code-gate";

const here = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
if (wantsHelp(argv)) { console.log(usage(import.meta.url)); process.exit(0); }
const at = argv.indexOf("--dir");
const dir = at >= 0 ? argv[at + 1] : join(here, "demo-repo");
try {
  const repo = await createDemoRepo(dir, { gateDir: here });
  console.log("Next:");
  console.log(`  npm start                       the gate, in another terminal`);
  console.log(`  npm run agent                   the built-in agent takes tasks.csv through the gate`);
  console.log(`  cd ${repo} && git log --show-notes=chap`);
} catch (e) {
  console.error(e.message);
  process.exit(1);
}
