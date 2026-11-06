// Stop the gate a command started in the background: `chap-code-gate stop`.
//
// propose and propose-branch start the gate when it is not running and
// write its process id to data/gate.pid in the gate project. This stops
// that process. A gate started with npm start in a terminal stops with
// Ctrl-C there.

import { readFile, rm } from "node:fs/promises";
import { join } from "node:path";

export async function main(argv, { gateDir, log = console.log } = {}) {
  const pidFile = join(gateDir, "data", "gate.pid");
  let pid = null;
  try { pid = Number((await readFile(pidFile, "utf8")).trim()); } catch { /* none */ }
  if (!pid) {
    log("No gate started in the background here (no data/gate.pid).");
    return 0;
  }
  try {
    process.kill(pid, "SIGTERM");
    log(`Stopped the gate (process ${pid}).`);
  } catch {
    log(`The gate (process ${pid}) was not running.`);
  }
  await rm(pidFile, { force: true });
  return 0;
}
