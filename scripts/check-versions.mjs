/**
 * One release version, checked everywhere it is written down.
 *
 * The release version appears in around sixty places: nine package manifests,
 * their cross-dependency pins, the MCP registry manifest, the default
 * `version` a server reports to its client, the reference servers, and the
 * documentation tables. Bumping them by hand is how `cli.ts` came to report a
 * version of its own, unrelated to the package it ships in.
 *
 * The root package.json holds the version. Everything else is checked against
 * it here.
 *
 *   node scripts/check-versions.mjs            fail on any disagreement
 *   node scripts/check-versions.mjs --write    rewrite them to agree
 *   node scripts/check-versions.mjs --set X    set the release version to X
 *
 * The check runs in CI, so a partial bump cannot reach a tag.
 *
 * Not covered, deliberately: the CHANGELOG, the release notes, and comments
 * that name the version which introduced a behaviour. Those are history and
 * must not move.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (p) => readFileSync(resolve(ROOT, p), "utf-8");

const args = process.argv.slice(2);
const setIndex = args.indexOf("--set");
const WRITE = args.includes("--write") || setIndex !== -1;
const VERSION = setIndex !== -1
  ? args[setIndex + 1]
  : JSON.parse(read("package.json")).version;

if (!/^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$/.test(VERSION ?? "")) {
  console.error(`Not a semantic version: ${VERSION}`);
  process.exit(2);
}

/**
 * Each site is a file, a regex with the version as its one capture group, and
 * a note saying what it is. `all` marks a pattern expected to match more than
 * once. A pattern that matches nothing is itself a failure: it means the file
 * was restructured and this check silently stopped covering it.
 */
const SITES = [
  // -- npm manifests -------------------------------------------------------
  ["package.json", /"version": "([^"]+)"/, "monorepo root, the source of truth"],
  ["packages/coordinator/package.json", /"version": "([^"]+)"/, "npm package version"],
  ["packages/coordinator-mcp/package.json", /"version": "([^"]+)"/, "npm package version"],
  ["packages/coordinator-a2a/package.json", /"version": "([^"]+)"/, "npm package version"],
  ["packages/coordinator-mcp/package.json", /"@brightbeamai\/chap-coordinator": "\^([^"]+)"/, "cross-dependency pin"],
  ["packages/coordinator-a2a/package.json", /"@brightbeamai\/chap-coordinator": "\^([^"]+)"/, "cross-dependency pin"],
  ["packages/coordinator-a2a/package.json", /"@brightbeamai\/chap-coordinator-mcp": "\^([^"]+)"/, "cross-dependency pin"],

  // -- Python manifests ----------------------------------------------------
  ["packages/coordinator-py/pyproject.toml", /^version = "([^"]+)"/m, "PyPI package version"],
  ...["ag2", "google-adk", "langgraph", "llama-index", "pydantic-ai"].flatMap(a => [
    [`packages/chap-${a}/pyproject.toml`, /^version *= *"([^"]+)"/m, "PyPI package version"],
    [`packages/chap-${a}/pyproject.toml`, /"chap-coordinator>=([^"]+)"/, "cross-dependency floor"],
  ]),

  // -- MCP registry --------------------------------------------------------
  ["server.json", /"version": "([^"]+)",\n  "websiteUrl"/, "registry server version"],
  ["server.json", /"identifier": "@brightbeamai\/chap-coordinator-mcp",\n      "version": "([^"]+)"/, "registry npm package version"],

  // -- container images ----------------------------------------------------
  ["Dockerfile.mcp", /^ARG CHAP_MCP_VERSION=(.+)$/m, "pinned image version"],

  // -- versions a running server reports to its client ---------------------
  ["packages/coordinator-mcp/src/cli.ts", /^const VERSION = "([^"]+)";/m, "version the MCP server reports"],
  ["packages/coordinator-mcp/src/index.ts", /version: options\.version \?\? "([^"]+)"/, "default serverInfo.version"],
  ["packages/coordinator-mcp/src/index.ts", /\/\*\* Server version\. Default: "([^"]+)"\. \*\//, "doc comment on the default"],
  ["packages/coordinator-a2a/src/card.ts", /version: *options\.version *\?\? *"([^"]+)"/, "default agent-card version"],
  ["packages/coordinator-py/chap_coordinator/transports/mcp_server.py", /^ {4}version: str = "([^"]+)",/m, "default serverInfo.version"],
  ["packages/coordinator-py/chap_coordinator/transports/a2a_server.py", /^ {4}version: str = "([^"]+)",/m, "default agent-card version"],

  // -- reference servers ---------------------------------------------------
  ["reference/mcp-server-ts/package.json", /"version": "([^"]+)"/, "reference server version"],
  ["reference/a2a-server-ts/package.json", /"version": "([^"]+)"/, "reference server version"],
  ["reference/mcp-server-ts/server.ts", /version: "([^"]+)"/, "reference server version"],
  ["reference/a2a-server-ts/server.ts", /version: "([^"]+)"/, "reference server version"],
  ["reference/mcp-server-py/server.py", /version="([^"]+)"/, "reference server version"],
  ["reference/a2a-server-py/server.py", /version="([^"]+)"/, "reference server version"],

  // -- usage examples a reader will copy -----------------------------------
  ["packages/coordinator-mcp/src/index.ts", /makeChapMcpServer\(coord, \{ name: "chap", version: "([^"]+)" \}\)/, "usage example"],
  ["packages/coordinator-mcp/README.md", /makeChapMcpServer\(coord, \{ name: "chap", version: "([^"]+)" \}\)/, "usage example"],
  ["packages/coordinator-py/chap_coordinator/transports/mcp_server.py", /make_chap_mcp_server\(coord, name="chap", version="([^"]+)"\)/, "usage example"],
  ["examples/drive-chap-from-claude-desktop.md", /@brightbeamai\/chap-coordinator-mcp@([0-9][^`\s]*)/, "pinned install example"],
  [".github/actions/chap-conformance/README.md", /chap-conformance@v([0-9][^\s`|]*)/, "pinned action tag"],

  // -- documentation tables ------------------------------------------------
  ["IMPLEMENTATIONS.md", /\| ([0-9]+\.[0-9]+\.[0-9]+) +\|/g, "implementations table", "all"],
  ...["ag2", "google-adk", "langgraph", "llama-index", "pydantic-ai"].flatMap(a => [
    [`packages/chap-${a}/README.md`, /`chap-coordinator>=([^`]+)`/, "dependency floor"],
    [`packages/chap-${a}/README.md`, /^- `chap-coordinator` ([0-9][^\s]*)$/m, "dependency table"],
  ]),
];

const problems = [];
const edits = new Map();

for (const [file, pattern, note, all] of SITES) {
  let text = edits.get(file) ?? read(file);
  const rx = all ? new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : pattern.flags + "g")
                 : pattern;
  const matches = [...text.matchAll(new RegExp(rx.source, rx.flags.includes("g") ? rx.flags : rx.flags + "g"))];

  if (matches.length === 0) {
    problems.push(`${file}: the pattern for "${note}" matched nothing. The file changed shape; fix scripts/check-versions.mjs.`);
    continue;
  }
  if (!all && matches.length > 1) {
    problems.push(`${file}: the pattern for "${note}" matched ${matches.length} times but is not marked "all". Make it specific.`);
    continue;
  }

  for (const m of matches) {
    if (m[1] === VERSION) continue;
    if (WRITE) {
      const replaced = m[0].replace(m[1], VERSION);
      text = text.slice(0, m.index) + replaced + text.slice(m.index + m[0].length);
      edits.set(file, text);
    } else {
      problems.push(`${file}: ${note} says ${m[1]}, expected ${VERSION}`);
    }
  }
  if (WRITE && !edits.has(file)) edits.set(file, text);
}

if (WRITE) {
  // Recompute once more so a multi-pattern file keeps every edit.
  for (const [file, text] of edits) writeFileSync(resolve(ROOT, file), text);
  if (problems.length) {
    for (const p of problems) console.error(p);
    process.exit(1);
  }
  console.log(`Set ${edits.size} files to ${VERSION}. Re-run without --write to confirm.`);
} else if (problems.length) {
  console.error(`Release version is ${VERSION} (packages/../package.json). Disagreements:\n`);
  for (const p of problems) console.error("  " + p);
  console.error(`\nRun: node scripts/check-versions.mjs --write`);
  process.exit(1);
} else {
  console.log(`Every version site agrees on ${VERSION} (${SITES.length} patterns checked).`);
}
