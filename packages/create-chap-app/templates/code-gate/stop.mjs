// Stop the gate a command started in the background: `npm run stop`.
//
// propose.mjs and propose-branch.mjs start the gate when it is not running
// and write its process id to data/gate.pid. This stops that process. A gate
// started with npm start in a terminal stops with Ctrl-C there.

import { readFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pidFile = join(here, "data", "gate.pid");

let pid = null;
try { pid = Number((await readFile(pidFile, "utf8")).trim()); } catch { /* none */ }
if (!pid) {
  console.log("No gate started in the background here (no data/gate.pid).");
} else {
  try {
    process.kill(pid, "SIGTERM");
    console.log(`Stopped the gate (process ${pid}).`);
  } catch {
    console.log(`The gate (process ${pid}) was not running.`);
  }
  await rm(pidFile, { force: true });
}
