/**
 * Keep the Python MCP tool schemas identical to the TypeScript ones.
 *
 * `packages/coordinator-py/.../mcp_schemas.py` says it mirrors
 * `packages/coordinator-mcp/src/schemas.ts` exactly. Two hand-maintained
 * copies of the same 195 parameter descriptions do not stay identical, so the
 * Python table is generated from the TypeScript one between the markers in
 * that file.
 *
 *   node scripts/sync-mcp-schemas.mjs           rewrite the Python table
 *   node scripts/sync-mcp-schemas.mjs --check   fail if it is out of date
 *
 * The check runs in CI. Edit schemas.ts, then run this without --check.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PY = resolve(ROOT, "packages/coordinator-py/chap_coordinator/transports/mcp_schemas.py");
const BEGIN = "# --- BEGIN GENERATED SCHEMAS (scripts/sync-mcp-schemas.mjs) ---";
const END = "# --- END GENERATED SCHEMAS ---";

const { SCHEMAS } = await import(resolve(ROOT, "packages/coordinator-mcp/src/schemas.ts"));

/** Render a JSON value as Python source. JSON and Python differ on three literals. */
function py(value, indent) {
  const pad = " ".repeat(indent);
  const inner = " ".repeat(indent + 4);
  if (value === null) return "None";
  if (value === true) return "True";
  if (value === false) return "False";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return JSON.stringify(value);
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return "[\n" + value.map(v => inner + py(v, indent + 4)).join(",\n") + ",\n" + pad + "]";
  }
  const keys = Object.keys(value);
  if (keys.length === 0) return "{}";
  return "{\n" + keys.map(k => `${inner}${JSON.stringify(k)}: ${py(value[k], indent + 4)}`).join(",\n")
    + ",\n" + pad + "}";
}

const body = Object.entries(SCHEMAS)
  .map(([tool, schema]) => `    ${JSON.stringify(tool)}: ${py(schema, 4)},`)
  .join("\n\n");

const generated = [
  BEGIN,
  "# Generated from packages/coordinator-mcp/src/schemas.ts. Do not edit by",
  "# hand: edit the TypeScript table and run scripts/sync-mcp-schemas.mjs.",
  "SCHEMAS: dict[str, dict[str, Any]] = {",
  body,
  "}",
  END,
].join("\n");

const current = readFileSync(PY, "utf-8");
const start = current.indexOf(BEGIN);
const stop = current.indexOf(END);
if (start === -1 || stop === -1) {
  console.error(`${PY} has no generated block. Add the marker comments first:\n  ${BEGIN}\n  ${END}`);
  process.exit(2);
}
const next = current.slice(0, start) + generated + current.slice(stop + END.length);

if (process.argv.includes("--check")) {
  if (next !== current) {
    console.error("The Python MCP schemas are out of date with schemas.ts.");
    console.error("Run: node scripts/sync-mcp-schemas.mjs");
    process.exit(1);
  }
  const tools = Object.keys(SCHEMAS).length;
  let params = 0, described = 0;
  for (const schema of Object.values(SCHEMAS)) {
    for (const prop of Object.values(schema.properties ?? {})) {
      params += 1;
      if (prop.description) described += 1;
    }
  }
  if (described !== params) {
    console.error(`${params - described} tool parameters have no description. Every one needs a purpose sentence.`);
    process.exit(1);
  }
  console.log(`Python MCP schemas match schemas.ts: ${tools} tools, ${params} parameters, all described.`);
} else {
  writeFileSync(PY, next);
  console.log(`Wrote ${Object.keys(SCHEMAS).length} tool schemas to ${PY}`);
}
