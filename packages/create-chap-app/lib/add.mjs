// Put CHAP into a project that exists:
//
//   npx create-chap-app add [<dir>] [--template <name>] [--into <subdir>] [--dry-run]
//
// The template's "add" section in its template.json says what goes in. Add
// writes the configuration and the entry points into a directory of their
// own, chap/ unless --into names another, so nothing the project has is
// overwritten. It merges the template's dependencies into package.json, or
// its chap- lines into requirements.txt; adds the section's scripts under
// names the project does not use; and keeps that directory's store and keys
// out of git. Where the template installs git hooks, they run the gate's
// check and then the hooks the repository ran before, and removing them puts
// those back. A repository whose prepare script runs husky gets the hooks'
// install after husky in it, since husky points core.hooksPath at its own
// directory each time npm install runs it.

import { access, chmod, copyFile, mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { DEFAULT_TEMPLATE, LANGUAGES, TEMPLATES_DIR, generate, gitIdentity, listTemplates } from "../index.mjs";
import { byVersion } from "./released.mjs";

const CHAP_NPM = /^@brightbeamai\/chap-/;
const requirementName = (line) => {
  const m = /^\s*([A-Za-z0-9][A-Za-z0-9._-]*)/.exec(line);
  return m && !/^\s*(#|-)/.test(line) ? m[1].toLowerCase().replace(/[-_.]+/g, "-") : null;
};
const floor = (spec) => /(\d+)\.(\d+)\.(\d+)/.exec(spec)?.[0] ?? null;
const forward = (from, to) => !floor(from) || !floor(to) || byVersion(floor(to), floor(from)) > 0;

async function readText(path) {
  try { return await readFile(path, "utf8"); } catch { return null; }
}

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}

/** `{dir}` in a template's add section, as the directory the project's scripts and paths name. */
const withDir = (text, sub) => text.replaceAll("{dir}", sub);

/** What adding the template to the project at `dir` would do. Changes nothing. */
export async function planAdd(dir, { template = null, into = null, templatesDir = TEMPLATES_DIR, identity = gitIdentity } = {}) {
  const root = resolve(dir);
  const pkgText = await readText(join(root, "package.json"));
  let pkg = null;
  try { pkg = pkgText === null ? null : JSON.parse(pkgText); } catch (e) { throw new Error(`${join(root, "package.json")} does not read as JSON (${e.message})`); }
  const requirements = await readText(join(root, "requirements.txt"));
  const kind = pkg ? "typescript" : requirements !== null || (await exists(join(root, "pyproject.toml"))) ? "python" : null;
  if (!kind) throw new Error(`${root} has no package.json, requirements.txt or pyproject.toml. add puts CHAP into a Node.js or Python project; npx create-chap-app <name> starts a new one.`);

  const templates = await listTemplates(templatesDir);
  const adders = templates.filter((t) => t.add && t.language === kind).map((t) => t.name);
  const name = template ?? (kind === "typescript" ? DEFAULT_TEMPLATE : adders[0]);
  if (!name) throw new Error(`No template in this release adds to a Python project yet. npx create-chap-app <name> --template <a Python template> starts a project of its own.`);
  const manifest = templates.find((t) => t.name === name);
  if (!manifest) throw new Error(`No template named ${name}. Templates: ${templates.map((t) => t.name).join(", ")}`);
  if (!manifest.add) throw new Error(`The ${name} template is a project of its own, with nothing to add to another; npx create-chap-app <name> --template ${name} starts one. Templates that add to a ${LANGUAGES[kind]} project: ${adders.join(", ") || "none yet"}.`);
  if (manifest.language !== kind) throw new Error(`The ${name} template runs on ${LANGUAGES[manifest.language]}, and ${root} is a ${LANGUAGES[kind]} project. Templates that add to it: ${adders.join(", ") || "none yet"}.`);

  const sub = (into ?? manifest.add.dir ?? "chap").replace(/\\/g, "/").replace(/\/+$/, "");
  if (!sub || isAbsolute(sub) || sub.split("/").some((part) => part === ".." || part === "")) throw new Error(`--into takes a directory inside the project, such as chap; ${into} is not one`);
  const target = join(root, sub);
  if (await exists(target) && (await readdir(target)).length) throw new Error(`${target} exists and is not empty; name another directory with --into.`);

  const scratch = await mkdtemp(join(tmpdir(), "chap-add-"));
  try {
    const fresh = join(scratch, "template");
    const me = identity(root);
    const { written, config } = await generate({
      name: pkg?.name ?? basename(root), template: name, profiles: manifest.default_profiles,
      human_name: me.name ?? undefined, human_email: me.email ?? undefined, project_dir: target,
    }, { templatesDir, targetDir: fresh });
    const missing = manifest.add.files.filter((f) => !written.includes(f));
    if (missing.length) throw new Error(`The ${name} template's add section names ${missing.join(", ")}, which it does not ship`);

    const changes = { files: manifest.add.files.map((f) => `${sub}/${f}`), packages: [], scripts: [], keptScripts: [], prepare: null, requirementsByHand: [], gitignore: [] };
    let nextPkg = null;
    if (kind === "typescript") {
      const tpl = JSON.parse(await readFile(join(fresh, "package.json"), "utf8"));
      nextPkg = structuredClone(pkg);
      for (const [dep, range] of Object.entries(tpl.dependencies ?? {})) {
        const section = ["dependencies", "devDependencies"].find((s) => pkg[s]?.[dep] !== undefined);
        const have = section ? pkg[section][dep] : undefined;
        if (have === range || (have !== undefined && (!CHAP_NPM.test(dep) || !forward(have, range)))) continue;
        (nextPkg[section ?? "dependencies"] ??= {})[dep] = range;
        changes.packages.push({ name: dep, from: have ?? null, to: range });
      }
      for (const [script, command] of Object.entries(manifest.add.scripts ?? {})) {
        const want = withDir(command, sub);
        const have = pkg.scripts?.[script];
        if (have === want) continue;
        if (have !== undefined) { changes.keptScripts.push({ name: script, want }); continue; }
        (nextPkg.scripts ??= {})[script] = want;
        changes.scripts.push(script);
      }
      const prepare = pkg.scripts?.prepare;
      const install = manifest.add.hooks ? withDir(manifest.add.scripts[manifest.add.hooks], sub) : null;
      if (install && prepare && /\bhusky\b/.test(prepare) && !prepare.includes("install-hooks")) {
        nextPkg.scripts.prepare = `${prepare} && ${install} --if-repo`;
        changes.prepare = nextPkg.scripts.prepare;
      }
    }
    let nextRequirements = null;
    if (kind === "python") {
      const pins = (await readText(join(fresh, "requirements.txt")) ?? "").split("\n").filter((l) => requirementName(l)?.startsWith("chap-")).map((l) => l.trim());
      if (requirements === null) changes.requirementsByHand = pins;
      else {
        const lines = requirements.split("\n");
        for (const pin of pins) {
          const at = lines.findIndex((l) => requirementName(l) === requirementName(pin));
          if (at < 0) { lines.splice(lines.at(-1) === "" ? lines.length - 1 : lines.length, 0, pin); changes.packages.push({ name: requirementName(pin), from: null, to: pin }); }
          else if (lines[at].trim() !== pin && forward(lines[at], pin)) { changes.packages.push({ name: requirementName(pin), from: lines[at].trim(), to: pin }); lines[at] = pin; }
        }
        nextRequirements = lines.join("\n");
        if (nextRequirements === requirements) nextRequirements = null;
      }
    }
    const ignore = (await readText(join(root, ".gitignore"))) ?? "";
    const present = new Set(ignore.split("\n").map((l) => l.trim()));
    changes.gitignore = (manifest.add.gitignore ?? []).map((g) => withDir(g, sub)).filter((g) => !present.has(g));
    const prepared = !!changes.prepare || (manifest.add.hooks && /install-hooks/.test(pkg?.scripts?.prepare ?? ""));
    const run = [
      kind === "typescript" ? "npm install" : "python -m pip install -r requirements.txt",
      ...(manifest.add.run ?? []).map((step) => withDir(step, sub)).filter((step) => !(prepared && manifest.add.hooks && step === `npm run ${manifest.add.hooks}`)),
    ];
    return { root, sub, target, fresh, scratch, name, manifest, kind, config, ignore, changes, nextPkg, nextRequirements, run, prepared };
  } catch (e) {
    await rm(scratch, { recursive: true, force: true });
    throw e;
  }
}

/** Carry out a plan from planAdd. */
export async function applyAdd(plan) {
  for (const rel of plan.manifest.add.files) {
    const to = join(plan.target, rel);
    await mkdir(dirname(to), { recursive: true });
    await copyFile(join(plan.fresh, rel), to);
    const mode = (await stat(join(plan.fresh, rel))).mode & 0o777;
    if (mode & 0o111) await chmod(to, mode);
  }
  if (plan.nextPkg && JSON.stringify(plan.nextPkg) !== JSON.stringify(JSON.parse(await readFile(join(plan.root, "package.json"), "utf8")))) {
    await writeFile(join(plan.root, "package.json"), `${JSON.stringify(plan.nextPkg, null, 2)}\n`);
  }
  if (plan.nextRequirements !== null) await writeFile(join(plan.root, "requirements.txt"), plan.nextRequirements);
  if (plan.changes.gitignore.length) {
    const head = plan.ignore && !plan.ignore.endsWith("\n") ? "\n" : "";
    await writeFile(join(plan.root, ".gitignore"), `${plan.ignore}${head}${plan.ignore ? "\n" : ""}# CHAP: what ${plan.sub}/ writes as it runs\n${plan.changes.gitignore.join("\n")}\n`);
  }
}

/** The plan in words. */
export function describe(plan, { dryRun = false } = {}) {
  const { changes } = plan;
  const would = (now, then) => (dryRun ? then : now);
  const lines = [`${would("Added", "To add")} the ${plan.name} template to ${plan.root}, in ${plan.sub}/:`, ...changes.files.map((f) => `  ${f}`)];
  if (changes.packages.length) lines.push("", `${plan.kind === "python" ? "requirements.txt" : "package.json"} dependencies:`, ...changes.packages.map((p) => `  ${p.name}: ${p.from ? `${p.from} -> ` : ""}${p.to}`));
  if (changes.requirementsByHand.length) lines.push("", "The project has no requirements.txt: add these to its dependencies by hand:", ...changes.requirementsByHand.map((l) => `  ${l}`));
  if (changes.scripts.length) lines.push("", `package.json scripts: ${changes.scripts.join(", ")}`);
  if (changes.keptScripts.length) lines.push("", "Scripts the project has already, kept as they are; the template's would be:", ...changes.keptScripts.map((s) => `  ${s.name}: ${s.want}`));
  if (changes.prepare) lines.push("", `prepare: ${changes.prepare}`, "husky points core.hooksPath at .husky/_ each time npm install runs it, so the gate's hooks are installed again after it, running husky's hooks after the gate's check.");
  if (changes.gitignore.length) lines.push("", `.gitignore: ${changes.gitignore.join(", ")}`);
  lines.push("", "Next:", ...plan.run.map((step) => `  ${step}`));
  if (plan.config?.port) lines.push("", `The desk is at http://localhost:${plan.config.port}/ once it runs; open it in a browser.`);
  if (plan.manifest.add.files.includes("AGENT_INSTRUCTIONS.md")) lines.push(`Paste ${plan.sub}/AGENT_INSTRUCTIONS.md into your agent's instructions, CLAUDE.md for Claude Code: it says who the agent is and how it asks for review.`);
  if (plan.manifest.add.hooks) lines.push(`The hooks come out with npx chap-code-gate install-hooks --gate ${plan.sub} --remove, which puts back the hooks the repository ran before${plan.prepared ? `; take the install out of prepare first, or npm install puts them in again` : ""}.`);
  if (dryRun) lines.push("", "Nothing was changed: --dry-run.");
  return lines.join("\n");
}

export function parseAddArgs(argv) {
  const args = { dir: null, template: null, into: null, dryRun: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const value = () => { const v = argv[++i]; if (v === undefined || v.startsWith("-")) throw new Error(`${a} needs a value`); return v; };
    if (a === "--template" || a === "-t") args.template = value();
    else if (a === "--into") args.into = value();
    else if (a === "--dry-run") args.dryRun = true;
    else if (a === "--help" || a === "-h") args.help = true;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}. npx create-chap-app add --help lists the options.`);
    else if (!args.dir) args.dir = a;
    else throw new Error(`Unexpected argument ${a}`);
  }
  return args;
}

export const ADD_USAGE = `Usage: npx create-chap-app add [<dir>] [--template <name>] [--into <subdir>] [--dry-run]

Puts CHAP into the Node.js or Python project in <dir>, the current
directory by default: the template's configuration and entry points in a
directory of their own, its dependencies and scripts in the project's, and
for the code gate, git hooks that run the repository's own after the gate's.

  --template  the template to add: code-gate (the default) or mcp-gate
  --into      the directory for CHAP's files, chap by default
  --dry-run   say what would change, and change nothing`;

export async function runAdd(argv, { say = console.log, templatesDir = TEMPLATES_DIR, identity = gitIdentity } = {}) {
  const args = parseAddArgs(argv);
  if (args.help) { say(ADD_USAGE); return 0; }
  const plan = await planAdd(args.dir ?? process.cwd(), { template: args.template, into: args.into, templatesDir, identity });
  try {
    if (!args.dryRun) await applyAdd(plan);
    say(describe(plan, { dryRun: args.dryRun }));
  } finally {
    await rm(plan.scratch, { recursive: true, force: true });
  }
  return 0;
}
