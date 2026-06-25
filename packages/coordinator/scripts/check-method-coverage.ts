/**
 * Schema-vs-code drift check.
 *
 * Reads two sources of truth and exits non-zero if they disagree:
 *   1. `packages/coordinator/src/methods.ts` - TypeScript MethodTable
 *   2. `schemas/profiles/chap-methods.schema.json` - JSON schema catalogue
 *
 * Catches:
 *   - A method removed from one source but not the other
 *   - A method marked `implemented` in the schema with no MethodTable entry
 *   - A MethodTable entry not declared in the schema
 *
 * Run from repo root:
 *   tsx packages/coordinator/scripts/check-method-coverage.ts
 *
 * This is the first step toward true schema-driven type generation. Once
 * the JSON schemas grow per-method param/result schemas, this script
 * will generate methods.ts directly. Until then, it enforces parity.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "..", "..", "..");

// ---- Source 1: MethodTable entries in methods.ts -------------------

const methodsTs = readFileSync(
  resolve(repoRoot, "packages/coordinator/src/methods.ts"),
  "utf-8",
);

const tableMatch = methodsTs.match(/export interface MethodTable\s*\{([\s\S]+?)\n\}/);
if (!tableMatch) {
  console.error("Could not find MethodTable interface in methods.ts");
  process.exit(2);
}
const tableBody = tableMatch[1];
const tableMethods = new Set<string>();
for (const m of tableBody.matchAll(/"([a-z]+\.[a-z_]+)"\s*:/g)) {
  tableMethods.add(m[1]);
}

// ---- Source 2: Method catalogue in chap-methods.schema.json --------

const schemaPath = resolve(repoRoot, "schemas/profiles/chap-methods.schema.json");
const schemaText = readFileSync(schemaPath, "utf-8");
const schemaJson = JSON.parse(schemaText);

interface MethodSpec {
  namespace:            string;
  type:                 string;
  summary:              string;
  required_scope:       string[];
  privileged:           boolean;
  since:                string;
  implementation_status: "implemented" | "spec-only" | "reference-only";
}

// The chap-methods schema embeds method entries as inline JSON objects.
// Each entry has a `namespace`, `implementation_status`, and possibly
// additional fields (e.g. `params_schema`, `result_schema`) that defeat
// a single combined regex. Parse in two steps: find every method name,
// then extract its implementation_status from its own line.
const catalogueEntries = new Map<string, MethodSpec>();
const nameRegex = /"([a-z]+\.[a-z_]+)"\s*:\s*\{\s*"namespace":\s*"([^"]+)"/g;
const lines = schemaText.split("\n");
for (const m of schemaText.matchAll(nameRegex)) {
  const name = m[1];
  const ns   = m[2];
  // Find the line containing this method's entry and pull the status.
  const line = lines.find(l => l.includes(`"${name}":`));
  if (!line) continue;
  const statusMatch = line.match(/"implementation_status":\s*"(implemented|spec-only|reference-only)"/);
  if (!statusMatch) continue;
  catalogueEntries.set(name, {
    namespace:            ns,
    type:                 "request",
    summary:              "",
    required_scope:       [],
    privileged:           false,
    since:                "core/1.0",
    implementation_status: statusMatch[1] as MethodSpec["implementation_status"],
  });
}

if (catalogueEntries.size === 0) {
  console.error("Could not parse method catalogue from chap-methods.schema.json");
  process.exit(2);
}

const schemaShipped = new Set<string>();
const schemaSpecOnly = new Set<string>();
for (const [name, spec] of catalogueEntries) {
  if (spec.implementation_status === "spec-only") schemaSpecOnly.add(name);
  else schemaShipped.add(name); // implemented or reference-only
}

// ---- Diff ----------------------------------------------------------

const inSchemaButNotTypes: string[] = [];
const inTypesButNotSchema: string[] = [];
const inTypesButSpecOnly: string[] = [];

for (const name of schemaShipped) {
  if (!tableMethods.has(name)) inSchemaButNotTypes.push(name);
}
for (const name of tableMethods) {
  if (!schemaShipped.has(name) && !schemaSpecOnly.has(name)) {
    inTypesButNotSchema.push(name);
  } else if (schemaSpecOnly.has(name)) {
    inTypesButSpecOnly.push(name);
  }
}

// ---- Report --------------------------------------------------------

const errors: string[] = [];

console.log(`Schema catalogue: ${catalogueEntries.size} total ` +
  `(${schemaShipped.size} shipped, ${schemaSpecOnly.size} spec-only)`);
console.log(`TypeScript MethodTable: ${tableMethods.size} entries`);
console.log("");

if (inSchemaButNotTypes.length > 0) {
  errors.push(
    `Schema marks these methods 'implemented' but methods.ts has no MethodTable entry:\n  ` +
    inSchemaButNotTypes.sort().join("\n  "),
  );
}

if (inTypesButNotSchema.length > 0) {
  errors.push(
    `methods.ts declares types for methods not in the schema catalogue at all:\n  ` +
    inTypesButNotSchema.sort().join("\n  "),
  );
}

if (inTypesButSpecOnly.length > 0) {
  errors.push(
    `methods.ts declares types for methods the schema marks 'spec-only':\n  ` +
    inTypesButSpecOnly.sort().join("\n  "),
  );
}

if (errors.length === 0) {
  console.log("✓ Method catalogue and TypeScript types are in sync.");
  process.exit(0);
} else {
  console.error("✗ Drift detected:\n");
  for (const e of errors) console.error(`  ${e}\n`);
  process.exit(1);
}
