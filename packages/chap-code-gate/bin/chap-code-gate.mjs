#!/usr/bin/env node
// chap-code-gate: the CHAP code gate's commands. `chap-code-gate --help`
// lists them.

import { COMMANDS, findGateDir, run } from "../lib/cli.mjs";

const USAGE = `Usage: chap-code-gate <command> [options] [--gate <dir>]

Commands:
  propose "<summary>"      propose the working tree's change for review
  propose-branch <range>   propose a branch for review before it is pushed
  verify [<range>]         check that commits were approved through the gate
  trust                    write the trust policy, chap-trust.json
  install-hooks            put a repository under the gate's git hooks
  hook <name>              run one of the git hooks, as git does
  push-notes               send the evidence notes with the commits
  report                   what the gate recorded, as Markdown
  stop                     stop a gate a command started in the background

--gate names the gate project, the directory that holds chap.config.json;
by default it is the nearest one up from the working directory.`;

const argv = process.argv.slice(2);
const at = argv.indexOf("--gate");
let gateDir = null;
if (at >= 0) {
  gateDir = argv[at + 1];
  argv.splice(at, 2);
}
const [name, ...rest] = argv;
if (!name || name === "--help" || name === "-h") {
  console.log(USAGE);
} else if (!COMMANDS[name]) {
  console.error(`Unknown command ${name}.\n\n${USAGE}`);
  process.exitCode = 2;
} else {
  // A refusal until the command has answered, so a hook that does not run to the end fails closed.
  process.exitCode = 1;
  run(name, rest, { gateDir: gateDir ?? findGateDir() })
    .then((code) => process.exit(code))
    .catch((e) => { console.error(`${name === "hook" ? "chap: " : ""}${e instanceof Error ? e.message : String(e)}`); process.exit(1); });
}
