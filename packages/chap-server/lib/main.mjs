// Whether a module is the script Node was asked to run. Node gives a
// module its real path, and gives process.argv[1] as typed, so the two are
// compared as real paths: a script reached through a symbolic link, a
// junction or a differently cased drive letter is still the script.

import { readFileSync, realpathSync } from "node:fs";
import { fileURLToPath } from "node:url";

/** Whether the module at `importMetaUrl` is the script this process runs. */
export function isMain(importMetaUrl) {
  if (!process.argv[1]) return false;
  try { return realpathSync(fileURLToPath(importMetaUrl)) === realpathSync(process.argv[1]); } catch { return false; }
}

/**
 * A script's help: the comment it opens with, without the comment marks.
 * Each script says there how it is run and what its options are.
 */
export function usage(importMetaUrl) {
  const lines = readFileSync(fileURLToPath(importMetaUrl), "utf8").split("\n");
  const head = [];
  for (const line of lines) {
    if (line.startsWith("#!")) continue;
    if (!line.startsWith("//")) break;
    head.push(line.replace(/^\/\/ ?/, ""));
  }
  return head.join("\n");
}

/** Whether the command line asks for help. */
export const wantsHelp = (argv = process.argv.slice(2)) => argv.includes("--help") || argv.includes("-h");
