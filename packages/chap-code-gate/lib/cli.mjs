// The code gate's commands, by name, for the chap-code-gate bin and for the
// entry points a gate project keeps beside its chap.config.json. Each runs
// against one gate project, the directory that holds chap.config.json, its
// keys/ and its data/, and gives back the exit code the process ends with.

import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { hook } from "./hook.mjs";
import { main as installHooks } from "./install-hooks.mjs";
import { main as propose } from "./propose.mjs";
import { main as proposeBranch } from "./propose-branch.mjs";
import { main as pushNotes } from "./push-notes.mjs";
import { main as report } from "./report.mjs";
import { main as stop } from "./stop.mjs";
import { main as trust } from "./trust.mjs";
import { main as verify } from "./verify.mjs";

export const COMMANDS = {
  propose,
  "propose-branch": proposeBranch,
  verify,
  trust,
  "install-hooks": installHooks,
  hook: ([stage, ...rest], options) => hook(stage, rest, process.cwd(), options),
  "push-notes": pushNotes,
  report,
  stop,
};

/** The gate project for a command run in `cwd`: the nearest directory up from it that holds chap.config.json, else `cwd`. */
export function findGateDir(cwd = process.cwd()) {
  for (let dir = resolve(cwd); ; dir = dirname(dir)) {
    if (existsSync(join(dir, "chap.config.json"))) return dir;
    if (dirname(dir) === dir) return resolve(cwd);
  }
}

/** Run the command `name` with its arguments against the gate project at `gateDir`. Returns the exit code. */
export async function run(name, argv, { gateDir, log = console.log } = {}) {
  const command = COMMANDS[name];
  if (!command) throw new Error(`Unknown command ${name}. Commands: ${Object.keys(COMMANDS).join(", ")}`);
  return command(argv, { gateDir, log });
}
