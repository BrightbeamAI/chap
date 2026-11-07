// Bring a generated project up to this release:
//
//   npx create-chap-app upgrade [<dir>] [--template <name>] [--dry-run] [--diff]
//
// The project stays its own. Upgrade
//   - moves the CHAP packages forward: package.json's @brightbeamai/chap-*
//     dependencies, or the chap- lines of requirements.txt, take the ranges
//     this release's template pins where those start later, and the ones the
//     template has gained are added;
//   - replaces a file the template ships, or a package.json script, only
//     where it is still as a released create-chap-app wrote it, by its hash
//     in released.json, and adds the files the template has gained;
//   - removes, on the same condition, a file a release wrote that the
//     template no longer ships. A project from before 0.3.5 carried the
//     server, the desk and the gate's library as copies, which
//     @brightbeamai/chap-server and @brightbeamai/chap-code-gate now hold.
//     hooks/ and gate-hook.mjs stay whatever they hold, since a repository
//     hooked up before 0.3.5 runs them by path;
//   - names every file it kept that differs from the template's, which
//     --diff shows with git diff, and leaves chap.config.json as it is apart
//     from the stamp, which it brings up to this release;
//   - adds keys/ to a .gitignore it keeps, where the template's has it and
//     the project's does not, since an agent's private key is kept there
//     and never belongs in the repository;
//   - adds the chap.config.json entries the template's own code needs and
//     the project's configuration lacks, such as the operator the
//     outbound-approval template's pause.py and resume.py act as;
//   - with --adopt-settings, writes the template's values for the settings
//     the project's configuration differs in, and moves the store aside
//     where they change how calls are signed or the chain, since a store's
//     workspace keeps the settings it was created with: the server starts a
//     new store, and the old one stays beside it.
// A project without a stamp, from 0.3.1 to 0.3.4, is recognised by the
// released copies it holds, or named with --template. --dry-run says what
// would change and changes nothing.

import { spawnSync } from "node:child_process";
import { chmod, copyFile, mkdir, mkdtemp, readFile, readdir, rename, rm, rmdir, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { TEMPLATES_DIR, VERSION, generate, listTemplates } from "../index.mjs";
import { byVersion, contentHash, loadReleased } from "./released.mjs";

/** Files upgrade never removes: a repository hooked up before 0.3.5 runs them by path. */
const ALWAYS_KEPT = /^(hooks\/[^/]+|gate-hook\.mjs)$/;
/** Files upgrade handles by their entries, never as a whole. */
const BY_ENTRY = new Set(["package.json", "package-lock.json", "requirements.txt"]);
/** chap.config.json entries that hold the project's own names, which upgrade does not compare. */
const OWN_ENTRIES = new Set(["template", "workspace", "profiles", "humans", "agent"]);
/** chap.config.json entries the template's own code needs, added where the project's configuration lacks them. */
const NEEDED_ENTRIES = new Set(["operator"]);
/** Settings a store's workspace keeps from its creation: changing one means a new store. */
const STORE_SETTINGS = new Set(["require_signatures", "signature_policy", "chain"]);
/** A CHAP package on npm. */
const CHAP_NPM = /^@brightbeamai\/chap-/;
/** A requirement line's distribution name, by PEP 503, with any extras and version left out. */
const requirementName = (line) => {
  const m = /^\s*([A-Za-z0-9][A-Za-z0-9._-]*)/.exec(line);
  return m && !/^\s*(#|-)/.test(line) ? m[1].toLowerCase().replace(/[-_.]+/g, "-") : null;
};

/** The lowest version a range or a requirement allows, as its first x.y.z; null where it names none. */
const floor = (spec) => /(\d+)\.(\d+)\.(\d+)/.exec(spec)?.[0] ?? null;
/** Whether `to` moves `from` forward: true where either names no version. */
const forward = (from, to) => !floor(from) || !floor(to) || byVersion(floor(to), floor(from)) > 0;

async function readOr(path, fallback = null) {
  try { return await readFile(path); } catch { return fallback; }
}

/** The project's files at `paths`, by hash: path → hash, for those it has. */
async function hashesOf(root, paths) {
  const out = new Map();
  for (const path of paths) {
    const bytes = await readOr(join(root, path));
    if (bytes) out.set(path, contentHash(bytes));
  }
  return out;
}

/**
 * The template a project without a stamp came from, and the release: the
 * template whose released copies the project holds most of, the one with
 * fewer files on a tie, and the release most of those copies are from.
 */
export async function recognise(root, released = loadReleased()) {
  let best = null;
  for (const [name, t] of Object.entries(released.templates)) {
    const own = await hashesOf(root, Object.keys(t.files));
    const votes = {};
    let score = 0;
    for (const [path, hash] of own) {
      const versions = t.files[path][hash];
      if (!versions) continue;
      score++;
      for (const v of versions) votes[v] = (votes[v] ?? 0) + 1;
    }
    const total = Object.keys(t.files).length;
    if (score && (!best || score > best.score || (score === best.score && total < best.total))) {
      const top = Math.max(...Object.values(votes));
      best = { name, score, total, version: Object.keys(votes).filter((v) => votes[v] === top).sort(byVersion).at(-1) };
    }
  }
  return best;
}

/** chap.config.json's text with the stamp set, keeping the rest of the text as it is. */
export function stamped(text, stamp) {
  const entry = `"template": { "name": ${JSON.stringify(stamp.name)}, "version": ${JSON.stringify(stamp.version)} }`;
  const replaced = /"template"\s*:\s*\{[^{}]*\}/.test(text)
    ? text.replace(/"template"\s*:\s*\{[^{}]*\}/, entry)
    : text.replace(/^(\s*\{)(\s*)/, (_, open, space) => `${open}${space || "\n  "}${entry},${space || "\n  "}`);
  try {
    const parsed = JSON.parse(replaced);
    if (parsed.template?.name === stamp.name && parsed.template?.version === stamp.version) return replaced;
  } catch { /* written whole below */ }
  const { template: _old, ...rest } = JSON.parse(text);
  return `${JSON.stringify({ template: stamp, ...rest }, null, 2)}\n`;
}

/**
 * What upgrading the project at `dir` would do, worked out on a copy of the
 * template generated with the project's own values. Changes nothing.
 */
export async function planUpgrade(dir, { template: named = null, templatesDir = TEMPLATES_DIR, released = loadReleased(), adopt = false } = {}) {
  const root = resolve(dir);
  const configText = await readOr(join(root, "chap.config.json"));
  if (!configText) throw new Error(`${root} has no chap.config.json. upgrade works in a project create-chap-app wrote; give its directory.`);
  let config;
  try { config = JSON.parse(configText.toString("utf8")); } catch (e) { throw new Error(`${join(root, "chap.config.json")} does not read as JSON (${e.message})`); }
  const pkgBytes = await readOr(join(root, "package.json"));
  let pkg = null;
  try { pkg = pkgBytes ? JSON.parse(pkgBytes.toString("utf8")) : null; } catch (e) { throw new Error(`${join(root, "package.json")} does not read as JSON (${e.message})`); }

  const templates = await listTemplates(templatesDir);
  let name = named ?? config.template?.name ?? null;
  let from = config.template?.name === name ? config.template?.version ?? null : null;
  let recognised = null;
  if (!name) {
    recognised = await recognise(root, released);
    if (!recognised) throw new Error(`${join(root, "chap.config.json")} has no template stamp, and no file in ${root} is as a release of create-chap-app wrote it. Name the template it came from with --template; npx create-chap-app --list names them.`);
    ({ name, version: from } = recognised);
  }
  const manifest = templates.find((t) => t.name === name);
  if (!manifest) throw new Error(`No template named ${name} in this release. Templates: ${templates.map((t) => t.name).join(", ")}`);

  // The template as this release writes it, with the project's own values.
  const scratch = await mkdtemp(join(tmpdir(), "chap-upgrade-"));
  try {
    return await plan({ root, scratch, config, configText: configText.toString("utf8"), pkg, name, from, recognised, manifest, templatesDir, released, adopt });
  } catch (e) {
    await rm(scratch, { recursive: true, force: true });
    throw e;
  }
}

async function plan({ root, scratch, config, configText, pkg, name, from, recognised, manifest, templatesDir, released, adopt }) {
  const fresh = join(scratch, "template");
  const human = config.humans?.[0] ?? {};
  const { written } = await generate({
    name: pkg?.name ?? basename(root), template: name, profiles: config.profiles ?? manifest.default_profiles,
    workspace: config.workspace, human_uri: human.uri, human_name: human.display_name, human_email: human.email,
    agent_uri: config.agent?.uri, review_at: config.review_at, project_dir: root,
  }, { templatesDir, targetDir: fresh });

  const known = released.templates[name] ?? { files: {}, scripts: {} };
  const stamp = { name, version: VERSION };
  // The configuration as upgraded: stamped, with the entries the template's
  // code needs, and, with --adopt-settings, the template's settings.
  const theirConfig = JSON.parse(await readFile(join(fresh, "chap.config.json"), "utf8"));
  const stampedConfig = stamped(configText, stamp);
  const ourStamped = JSON.parse(stampedConfig);
  const settings = [];
  for (const [key, value] of Object.entries(theirConfig)) {
    if (OWN_ENTRIES.has(key) || NEEDED_ENTRIES.has(key) || JSON.stringify(ourStamped[key]) === JSON.stringify(value)) continue;
    settings.push({ key, yours: ourStamped[key], template: value });
  }
  const added = [...NEEDED_ENTRIES].filter((key) => theirConfig[key] !== undefined && ourStamped[key] === undefined);
  const adopted = adopt ? settings : [];
  let nextConfig = stampedConfig;
  if (added.length || adopted.length) {
    const next = { ...ourStamped };
    for (const key of added) next[key] = theirConfig[key];
    for (const s of adopted) next[s.key] = s.template;
    nextConfig = `${JSON.stringify(next, null, 2)}\n`;
  }
  // A store whose workspace was created under the settings adopted keeps
  // them, and holds members joined under them: it is moved aside.
  let storeMove = null;
  if (adopted.some((s) => STORE_SETTINGS.has(s.key)) && typeof config.store === "string" && config.store && config.store !== ":memory:") {
    const from = resolve(root, config.store);
    if (await readOr(from)) storeMove = { from, to: `${from}.before-${VERSION}` };
  }
  const files = { add: [], replace: [], remove: [], kept: [], keptByRule: [], differs: [] };
  for (const rel of written.filter((w) => !BY_ENTRY.has(w))) {
    const ours = rel === "chap.config.json" ? Buffer.from(nextConfig) : await readOr(join(root, rel));
    const theirs = await readFile(join(fresh, rel));
    if (!ours) { files.add.push(rel); continue; }
    const hash = contentHash(ours);
    if (hash === contentHash(theirs)) continue;
    if (rel !== "chap.config.json" && known.files[rel]?.[hash]) files.replace.push(rel);
    else files.differs.push(rel);
  }
  for (const [rel, hashes] of Object.entries(known.files)) {
    if (written.includes(rel) || BY_ENTRY.has(rel)) continue;
    const ours = await readOr(join(root, rel));
    if (!ours) continue;
    if (ALWAYS_KEPT.test(rel)) files.keptByRule.push(rel);
    else if (hashes[contentHash(ours)]) files.remove.push(rel);
    else files.kept.push(rel);
  }

  // package.json: the CHAP packages forward, the template's scripts where the project's are a release's.
  const packages = [];
  const scripts = { add: [], replace: [], remove: [], kept: [] };
  let nextPkg = null;
  if (pkg && written.includes("package.json")) {
    const tpl = JSON.parse(await readFile(join(fresh, "package.json"), "utf8"));
    nextPkg = structuredClone(pkg);
    const sectionOf = (dep) => ["dependencies", "devDependencies"].find((s) => pkg[s]?.[dep] !== undefined) ?? null;
    for (const field of ["dependencies", "devDependencies"]) {
      for (const [dep, range] of Object.entries(tpl[field] ?? {})) {
        const section = sectionOf(dep);
        const have = section ? pkg[section][dep] : undefined;
        if (have === range || (have !== undefined && (!CHAP_NPM.test(dep) || !forward(have, range)))) continue;
        (nextPkg[section ?? field] ??= {})[dep] = range;
        packages.push({ name: dep, from: have ?? null, to: range });
      }
    }
    const listed = new Set([...Object.keys(tpl.dependencies ?? {}), ...Object.keys(tpl.devDependencies ?? {})]);
    for (const section of ["dependencies", "devDependencies"]) {
      for (const [dep, have] of Object.entries(pkg[section] ?? {})) {
        if (!CHAP_NPM.test(dep) || listed.has(dep) || !forward(have, VERSION)) continue;
        nextPkg[section][dep] = `^${VERSION}`;
        packages.push({ name: dep, from: have, to: `^${VERSION}` });
      }
    }
    for (const [script, command] of Object.entries(tpl.scripts ?? {})) {
      const have = pkg.scripts?.[script];
      if (have === command) continue;
      if (have === undefined) scripts.add.push(script);
      else if (known.scripts[script]?.[have]) scripts.replace.push(script);
      else { scripts.kept.push({ name: script, template: command }); continue; }
      (nextPkg.scripts ??= {})[script] = command;
    }
    for (const [script, have] of Object.entries(pkg.scripts ?? {})) {
      if (tpl.scripts?.[script] === undefined && known.scripts[script]?.[have]) {
        scripts.remove.push(script);
        delete nextPkg.scripts[script];
      }
    }
  }

  // requirements.txt: each chap- line the template pins takes the template's line.
  let nextRequirements = null;
  if (written.includes("requirements.txt")) {
    const tplLines = (await readFile(join(fresh, "requirements.txt"), "utf8")).split("\n").filter((l) => requirementName(l)?.startsWith("chap-"));
    const ourText = (await readOr(join(root, "requirements.txt")))?.toString("utf8") ?? null;
    if (ourText === null) files.add.push("requirements.txt");
    else {
      const pins = new Map(tplLines.map((l) => [requirementName(l), l.trim()]));
      const seen = new Set();
      const lines = ourText.split("\n").map((line) => {
        const req = requirementName(line);
        if (!req || !pins.has(req)) return line;
        seen.add(req);
        if (line.trim() === pins.get(req) || !forward(line, pins.get(req))) return line;
        packages.push({ name: req, from: line.trim(), to: pins.get(req) });
        return pins.get(req);
      });
      for (const [req, line] of pins) if (!seen.has(req)) { lines.splice(lines.at(-1) === "" ? lines.length - 1 : lines.length, 0, line); packages.push({ name: req, from: null, to: line }); }
      nextRequirements = lines.join("\n");
      if (nextRequirements === ourText) nextRequirements = null;
    }
  }

  // A private key never belongs in git. A .gitignore a release wrote is
  // replaced above; one the owner changed is kept, and gains keys/ where
  // the template's has it and the project's ignores no keys/ directory.
  const ignoreKeys = written.includes(".gitignore") && files.differs.includes(".gitignore")
    && (await readFile(join(fresh, ".gitignore"), "utf8")).split("\n").some((l) => l.trim() === "keys/")
    && !(await readFile(join(root, ".gitignore"), "utf8")).split("\n").some((l) => /^\/?keys\/?\**$/.test(l.trim()));

  return {
    root, fresh, scratch, name, from, recognised, stamp, files, packages, scripts, settings, added, adopted, storeMove, ignoreKeys, language: manifest.language,
    devRequirements: written.includes("requirements-dev.txt"),
    nextPkg: nextPkg && JSON.stringify(nextPkg) !== JSON.stringify(pkg) ? nextPkg : null,
    nextRequirements,
    nextConfig: nextConfig !== configText ? nextConfig : null,
  };
}

/** Remove the directories under `root` that `removed` left empty, deepest first. */
async function pruneEmpty(root, removed) {
  const dirs = [...new Set(removed.map((rel) => dirname(rel)).filter((d) => d !== "."))].sort((a, b) => b.split("/").length - a.split("/").length);
  for (const rel of dirs) {
    for (let d = rel; d !== "."; d = dirname(d)) {
      const path = join(root, d);
      try { if ((await readdir(path)).length) break; await rmdir(path); } catch { break; }
    }
  }
}

/** Carry out a plan from planUpgrade. */
export async function applyUpgrade(plan) {
  const { root, fresh, files } = plan;
  for (const rel of [...files.add, ...files.replace]) {
    const to = join(root, rel);
    await mkdir(dirname(to), { recursive: true });
    await copyFile(join(fresh, rel), to);
    const mode = (await stat(join(fresh, rel))).mode & 0o777;
    if (mode & 0o111) await chmod(to, mode);
  }
  for (const rel of files.remove) await rm(join(root, rel), { force: true });
  await pruneEmpty(root, files.remove);
  if (plan.nextPkg) await writeFile(join(root, "package.json"), `${JSON.stringify(plan.nextPkg, null, 2)}\n`);
  if (plan.nextRequirements !== null) await writeFile(join(root, "requirements.txt"), plan.nextRequirements);
  if (plan.nextConfig) await writeFile(join(root, "chap.config.json"), plan.nextConfig);
  if (plan.storeMove) {
    for (const suffix of ["", "-wal", "-shm"]) {
      if (await readOr(plan.storeMove.from + suffix)) await rename(plan.storeMove.from + suffix, plan.storeMove.to + suffix);
    }
  }
  if (plan.ignoreKeys) {
    const text = await readFile(join(root, ".gitignore"), "utf8");
    await writeFile(join(root, ".gitignore"), `${text}${text === "" || text.endsWith("\n") ? "" : "\n"}# CHAP: the agent's private key\nkeys/\n`);
  }
}

/** The plan in words. */
export function describe(plan, { dryRun = false } = {}) {
  const { files, scripts, packages } = plan;
  const lines = [];
  const would = (now, then) => (dryRun ? then : now);
  const where = plan.recognised
    ? `recognised as the ${plan.name} template from create-chap-app ${plan.from}, by the ${plan.recognised.score} files it holds as that release wrote them`
    : `the ${plan.name} template${plan.from ? ` from create-chap-app ${plan.from}` : ""}`;
  lines.push(`${plan.root}: ${where}, ${would("brought up to", "to be brought up to")} ${VERSION}.`);
  const list = (title, items) => { if (items.length) lines.push("", title, ...items.map((i) => `  ${i}`)); };
  list(would("Packages moved forward:", "Packages to move forward:"), packages.map((p) => (p.from ? `${p.name}: ${p.from} -> ${p.to}` : `${p.name}: ${p.to}, added`)));
  list(would("Replaced, each as a release wrote it:", "To replace, each as a release wrote it:"), files.replace);
  list(would("Added, new in the template:", "To add, new in the template:"), files.add);
  list(would("Removed, each as a release wrote it; the template no longer ships them:", "To remove, each as a release wrote it; the template no longer ships them:"), files.remove);
  const scriptWords = [
    ...scripts.replace.map((s) => `${s}: now the template's command; the project's was a release's`),
    ...scripts.add.map((s) => `${s}: added`),
    ...scripts.remove.map((s) => `${s}: removed, a release's command the template no longer has`),
  ];
  list(would("Scripts in package.json:", "Scripts in package.json, to change:"), scriptWords);
  list("Kept, since each differs from the template's and from every released copy (--diff shows how):", files.differs);
  list("Kept, no longer in the template, since each differs from every released copy:", files.kept);
  list("Kept whatever they hold, since a repository hooked up before 0.3.5 runs them by path:", files.keptByRule);
  list("Scripts kept as they are, each different from the template's and from every release's:", scripts.kept.map((s) => `${s.name}: the template's is ${JSON.stringify(s.template)}`));
  if (plan.nextConfig) lines.push("", `chap.config.json: ${would("stamped", "to be stamped")} ${plan.stamp.name} ${plan.stamp.version}.`);
  const why = { operator: "the operator the template's pause.py and resume.py act as" };
  for (const key of plan.added) lines.push(`chap.config.json: ${key} ${would("added", "to be added")}, ${why[key] ?? "which the template's code reads"}.`);
  if (plan.adopted.length) {
    lines.push("", `chap.config.json ${would("takes", "to take")} the template's settings:`,
      ...plan.adopted.map((s) => `  ${s.key}: ${JSON.stringify(s.template)} (was ${s.yours === undefined ? "not set" : JSON.stringify(s.yours)})`));
    if (plan.storeMove) lines.push(`The store ${would("was moved", "to be moved")} aside to ${plan.storeMove.to}, since its workspace keeps the settings it was created with; the server starts a new store, and chap-analytics still reads the old one.`);
  }
  if (plan.ignoreKeys) lines.push("", `.gitignore: keys/ ${would("added", "to be added")}, so the agent's private key stays out of the repository.`);
  if (plan.settings.length && !plan.adopted.length) {
    lines.push("", "chap.config.json keeps your settings. The template's differ in:",
      ...plan.settings.map((s) => `  ${s.key}: ${JSON.stringify(s.template)} (yours: ${s.yours === undefined ? "not set" : JSON.stringify(s.yours)})`),
      "The project runs as before with them. The template's tests check its own settings, so a test that reads one of these fails until chap.config.json has the template's value.",
      "Do not change require_signatures, signature_policy or chain by hand on the store you have: its workspace keeps the settings it was created with, and a store made with signatures off holds its members with no keys, so their signed calls are refused.",
      "Run upgrade again with --adopt-settings to take the template's settings: it moves the store aside, the server starts a new one, and the old one stays beside it.");
  }
  const changed = packages.length || files.add.length || files.replace.length || files.remove.length || scripts.add.length || scripts.replace.length || scripts.remove.length || plan.ignoreKeys
    || plan.added.length || plan.adopted.length;
  if (!changed) lines.push("", "Nothing to bring forward: the project has what this release's template has.");
  if (dryRun) lines.push("", "Nothing was changed: --dry-run.");
  else if (changed) {
    const next = plan.language !== "python" ? "Next: npm install, then npm test."
      : plan.devRequirements ? "Next: python -m pip install -r requirements-dev.txt, which installs requirements.txt and pytest, then python -m pytest tests -q."
        : "Next: python -m pip install -r requirements.txt, then run the project's tests.";
    lines.push("", next);
  }
  return lines.join("\n");
}

/**
 * The kept files' differences from the template's, as git diff shows them,
 * between yours/<path> and template/<path>; chap.config.json as stamped.
 */
async function diffs(plan) {
  const out = [];
  for (const rel of plan.files.differs) {
    const yours = join(plan.scratch, "yours", rel);
    await mkdir(dirname(yours), { recursive: true });
    await writeFile(yours, rel === "chap.config.json" && plan.nextConfig ? plan.nextConfig : await readFile(join(plan.root, rel)));
    const r = spawnSync("git", ["diff", "--no-index", "--no-color", "--src-prefix=", "--dst-prefix=", "--", `yours/${rel}`, `template/${rel}`], { cwd: plan.scratch, encoding: "utf8" });
    if (r.error) return [`--diff needs git, which did not run: ${r.error.message}`];
    out.push(r.stdout.trimEnd());
  }
  return out;
}

export function parseUpgradeArgs(argv) {
  const args = { dir: null, template: null, dryRun: false, diff: false, adopt: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--template" || a === "-t") { args.template = argv[++i]; if (!args.template || args.template.startsWith("-")) throw new Error(`${a} needs a value`); }
    else if (a === "--dry-run") args.dryRun = true;
    else if (a === "--diff") args.diff = true;
    else if (a === "--adopt-settings") args.adopt = true;
    else if (a === "--help" || a === "-h") args.help = true;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}. npx create-chap-app upgrade --help lists the options.`);
    else if (!args.dir) args.dir = a;
    else throw new Error(`Unexpected argument ${a}`);
  }
  return args;
}

export const UPGRADE_USAGE = `Usage: npx create-chap-app upgrade [<dir>] [--template <name>] [--dry-run] [--diff] [--adopt-settings]

Brings the project in <dir>, the current directory by default, up to this
release: the CHAP packages forward, and each file or script the template
ships replaced only where it is as a released create-chap-app wrote it.
Files it keeps because they differ are named.

  --template  the template the project came from, where chap.config.json has no stamp
  --dry-run   say what would change, and change nothing
  --diff      show how each kept file differs from the template's, with git diff
  --adopt-settings
              take the template's settings in chap.config.json, and move the
              store aside where they change signing or the chain`;

export async function runUpgrade(argv, { say = console.log, templatesDir = TEMPLATES_DIR, released } = {}) {
  const args = parseUpgradeArgs(argv);
  if (args.help) { say(UPGRADE_USAGE); return 0; }
  const plan = await planUpgrade(args.dir ?? process.cwd(), { template: args.template, templatesDir, released: released ?? loadReleased(), adopt: args.adopt });
  try {
    if (!args.dryRun) await applyUpgrade(plan);
    say(describe(plan, { dryRun: args.dryRun }));
    if (args.diff) for (const d of await diffs(plan)) if (d) say(`\n${d}`);
  } finally {
    await rm(plan.scratch, { recursive: true, force: true });
  }
  return 0;
}
