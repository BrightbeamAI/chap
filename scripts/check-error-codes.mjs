/**
 * Every error code the normative documents allocate exists in both references.
 *
 * SPECIFICATION 13.3 says each profile's own error table is authoritative, so
 * the tables in profiles/*.md and the JSON-RPC table in 13.3 are the
 * allocation. A code named there and missing from a reference is a promise the
 * reference does not keep, which is the defect #138 is about: -32701 for
 * envelope-id replay and -32401 for a non-monotonic timestamp were both
 * specified as MUSTs and allocated in neither reference.
 *
 * The check runs the other way too. A code in one reference and not the other
 * is a cross-language divergence waiting to be returned to a client.
 *
 *   node scripts/check-error-codes.mjs
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(resolve(root, p), "utf-8");

/** Codes in a markdown error table: a row whose first cell is the code. */
function tableCodes(text) {
  const found = new Map();
  for (const line of text.split("\n")) {
    const m = /^\|\s*`?(-32\d{3})`?\s*\|/.exec(line);
    if (m) found.set(Number(m[1]), line.trim());
  }
  return found;
}

const specified = new Map();
for (const [code] of tableCodes(read("SPECIFICATION.md"))) specified.set(code, "SPECIFICATION.md");
for (const file of readdirSync(resolve(root, "profiles")).filter(f => f.endsWith(".md"))) {
  for (const [code] of tableCodes(read(`profiles/${file}`))) {
    if (!specified.has(code)) specified.set(code, `profiles/${file}`);
  }
}

const allocated = (text) => new Set(
  [...text.matchAll(/(-32\d{3})(?!\d)/g)]
    .filter(m => !/^\s*(\/\/|#)/.test(text.slice(text.lastIndexOf("\n", m.index) + 1, m.index)))
    .map(m => Number(m[1])));

const ts = allocated(read("packages/coordinator/src/jsonrpc.ts"));
const py = allocated(read("packages/coordinator-py/chap_coordinator/jsonrpc.py"));

const problems = [];
for (const [code, where] of [...specified].sort((a, b) => b[0] - a[0])) {
  const missing = [!ts.has(code) && "TypeScript", !py.has(code) && "Python"].filter(Boolean);
  if (missing.length) {
    problems.push(`  ${code} is allocated in ${where} and missing from ${missing.join(" and ")}`);
  }
}
for (const code of [...new Set([...ts, ...py])].sort((a, b) => b - a)) {
  if (ts.has(code) !== py.has(code)) {
    problems.push(`  ${code} exists in ${ts.has(code) ? "TypeScript" : "Python"} alone`);
  }
}

if (problems.length) {
  console.error("Error codes disagree between the documents and the references:\n");
  console.error(problems.join("\n"));
  console.error("\nAllocate the code in both references, or take it out of the normative table.");
  process.exit(1);
}
console.log(`Every allocated error code exists in both references: ${specified.size} codes checked.`);
