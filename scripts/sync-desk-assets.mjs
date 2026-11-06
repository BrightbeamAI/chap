/**
 * Keep the desk the Python package ships the same as the desk the Node
 * server serves.
 *
 * The desk is one set of files. The Node server serves them from
 * packages/chap-server/desk. An installed Python package cannot reach that
 * directory, so chap-coordinator ships a copy as package data, in
 * packages/coordinator-py/chap_coordinator/desk, and this script writes it.
 *
 *   node scripts/sync-desk-assets.mjs           make the copy match
 *   node scripts/sync-desk-assets.mjs --check   fail if it does not
 *
 * The check runs in CI. Edit the desk where it lives, then run this without
 * --check. The Python server serves the files at the top of the directory,
 * so a subdirectory in the desk is refused here, where it is seen.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = resolve(ROOT, "packages/chap-server/desk");
const COPY = resolve(ROOT, "packages/coordinator-py/chap_coordinator/desk");
const CHECK = process.argv.includes("--check");

if (!existsSync(SOURCE)) {
  console.error(`No desk to copy: ${relative(ROOT, SOURCE)} does not exist.`);
  process.exit(2);
}

/** The files of a directory, by name; dot files are left out. */
function files(dir) {
  const out = new Map();
  if (!existsSync(dir)) return out;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name.startsWith(".")) continue;
    if (!entry.isFile()) {
      console.error(`${relative(ROOT, join(dir, entry.name))} is not a file. The Python server serves the desk's top-level files only; ` +
                    "move it, or teach chap_coordinator/server.py and this script to serve it.");
      process.exit(2);
    }
    out.set(entry.name, readFileSync(join(dir, entry.name)));
  }
  return out;
}

const want = files(SOURCE);
const have = files(COPY);
if (!want.has("index.html")) {
  console.error(`${relative(ROOT, SOURCE)} has no index.html, so it is not the desk.`);
  process.exit(2);
}

const missing = [...want.keys()].filter((name) => !have.has(name));
const changed = [...want.keys()].filter((name) => have.has(name) && !have.get(name).equals(want.get(name)));
const extra = [...have.keys()].filter((name) => !want.has(name));
const from = relative(ROOT, SOURCE);
const to = relative(ROOT, COPY);

if (CHECK) {
  if (missing.length || changed.length || extra.length) {
    for (const name of missing) console.error(`Missing from ${to}: ${name}`);
    for (const name of changed) console.error(`Differs from ${from}: ${to}/${name}`);
    for (const name of extra) console.error(`Not in ${from}: ${to}/${name}`);
    console.error("Run: node scripts/sync-desk-assets.mjs");
    process.exit(1);
  }
  console.log(`${to} matches ${from}: ${want.size} files.`);
} else {
  mkdirSync(COPY, { recursive: true });
  for (const name of [...missing, ...changed]) writeFileSync(join(COPY, name), want.get(name));
  for (const name of extra) rmSync(join(COPY, name));
  const done = missing.length + changed.length + extra.length;
  console.log(done
    ? `${to} now matches ${from}: ${missing.length} added, ${changed.length} updated, ${extra.length} removed.`
    : `${to} already matches ${from}: ${want.size} files.`);
}
