// The entry points a gate project keeps beside its chap.config.json, so an
// agent working in another repository, a hook and a CI job can name a file
// in the project. Each runs one command against the project its file is in.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { run } from "./cli.mjs";
import { hook } from "./hook.mjs";

const gateDirOf = (importMetaUrl) => dirname(fileURLToPath(importMetaUrl));

/** Run the command `name` for the gate project that holds the file at `importMetaUrl`, then exit with its code. */
export async function entry(name, importMetaUrl, argv = process.argv.slice(2)) {
  try {
    process.exit(await run(name, argv, { gateDir: gateDirOf(importMetaUrl) }));
  } catch (e) {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(1);
  }
}

/**
 * Run the git hook named in argv for the gate project that holds the file
 * at `importMetaUrl`, then exit with its code. Until the hook has given its
 * answer the exit code is a refusal, so a hook that does not run to the end
 * fails closed.
 */
export async function hookEntry(importMetaUrl, argv = process.argv.slice(2)) {
  process.exitCode = 1;
  try {
    process.exit(await hook(argv[0], argv.slice(1), process.cwd(), { gateDir: gateDirOf(importMetaUrl) }));
  } catch (e) {
    process.stderr.write(`chap: ${e instanceof Error ? e.message : String(e)}\n`);
    process.exit(1);
  }
}
