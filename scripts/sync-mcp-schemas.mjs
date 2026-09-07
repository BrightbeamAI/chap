/**
 * Keep the Python MCP tool tables identical to the TypeScript ones.
 *
 * `packages/coordinator-py/.../mcp_schemas.py` and `.../mcp_tools.py` each say
 * they mirror their TypeScript counterpart exactly. Hand-maintained copies of
 * 195 parameter descriptions and 39 tool descriptions do not stay identical, so
 * both Python tables are generated from the TypeScript ones between markers.
 *
 *   node scripts/sync-mcp-schemas.mjs           rewrite the Python tables
 *   node scripts/sync-mcp-schemas.mjs --check   fail if either is out of date
 *
 * The check runs in CI. Edit the TypeScript, then run this without --check.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const TRANSPORTS = resolve(ROOT, "packages/coordinator-py/chap_coordinator/transports");
const SCHEMAS_PY = resolve(TRANSPORTS, "mcp_schemas.py");
const TOOLS_PY = resolve(TRANSPORTS, "mcp_tools.py");

const SCHEMAS_BEGIN = "# --- BEGIN GENERATED SCHEMAS (scripts/sync-mcp-schemas.mjs) ---";
const SCHEMAS_END = "# --- END GENERATED SCHEMAS ---";
const TOOLS_BEGIN = "# --- BEGIN GENERATED DESCRIPTIONS (scripts/sync-mcp-schemas.mjs) ---";
const TOOLS_END = "# --- END GENERATED DESCRIPTIONS ---";

const { SCHEMAS } = await import(resolve(ROOT, "packages/coordinator-mcp/src/schemas.ts"));
const { TOOL_DESCRIPTIONS } = await import(resolve(ROOT, "packages/coordinator-mcp/src/tools.ts"));

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

const schemasBlock = [
  SCHEMAS_BEGIN,
  "# Generated from packages/coordinator-mcp/src/schemas.ts. Do not edit by",
  "# hand: edit the TypeScript table and run scripts/sync-mcp-schemas.mjs.",
  "SCHEMAS: dict[str, dict[str, Any]] = {",
  Object.entries(SCHEMAS)
    .map(([tool, schema]) => `    ${JSON.stringify(tool)}: ${py(schema, 4)},`)
    .join("\n\n"),
  "}",
  SCHEMAS_END,
].join("\n");

const toolsBlock = [
  TOOLS_BEGIN,
  "# Generated from packages/coordinator-mcp/src/tools.ts. Do not edit by",
  "# hand: edit the TypeScript table and run scripts/sync-mcp-schemas.mjs.",
  "TOOL_DESCRIPTIONS: dict[str, str] = {",
  Object.entries(TOOL_DESCRIPTIONS)
    .map(([tool, text]) => `    ${JSON.stringify(tool)}:\n        ${JSON.stringify(text)},`)
    .join("\n"),
  "}",
  TOOLS_END,
].join("\n");

/** Splice a generated block into a file between its markers. */
function splice(path, begin, end, block) {
  const current = readFileSync(path, "utf-8");
  const start = current.indexOf(begin);
  const stop = current.indexOf(end);
  if (start === -1 || stop === -1) {
    console.error(`${path} has no generated block. Add the marker comments first:\n  ${begin}\n  ${end}`);
    process.exit(2);
  }
  return { current, next: current.slice(0, start) + block + current.slice(stop + end.length) };
}

const targets = [
  { path: SCHEMAS_PY, ...splice(SCHEMAS_PY, SCHEMAS_BEGIN, SCHEMAS_END, schemasBlock) },
  { path: TOOLS_PY, ...splice(TOOLS_PY, TOOLS_BEGIN, TOOLS_END, toolsBlock) },
];

if (process.argv.includes("--check")) {
  const stale = targets.filter(t => t.next !== t.current);
  if (stale.length) {
    for (const t of stale) console.error(`Out of date with the TypeScript table: ${t.path}`);
    console.error("Run: node scripts/sync-mcp-schemas.mjs");
    process.exit(1);
  }

  const tools = Object.keys(SCHEMAS);
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

  const undescribed = tools.filter(t => !TOOL_DESCRIPTIONS[t]);
  if (undescribed.length) {
    console.error(`Tools with no description in tools.ts: ${undescribed.join(", ")}`);
    process.exit(1);
  }
  const orphaned = Object.keys(TOOL_DESCRIPTIONS).filter(t => !SCHEMAS[t]);
  if (orphaned.length) {
    console.error(`tools.ts describes tools that have no schema: ${orphaned.join(", ")}`);
    process.exit(1);
  }

  console.log(`Python MCP tables match the TypeScript ones: ${tools.length} tools, ` +
              `${params} parameters, all described.`);
} else {
  for (const t of targets) writeFileSync(t.path, t.next);
  console.log(`Wrote ${Object.keys(SCHEMAS).length} tool schemas and descriptions to ${TRANSPORTS}`);
}
