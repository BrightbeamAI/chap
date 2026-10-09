#!/usr/bin/env node
// create-chap-app: generate a CHAP project from a template.
//
//   npx create-chap-app my-gate
//   npx create-chap-app my-gate --template mcp-gate --profiles core/1.0,review/1.0 --yes
//
// Three questions: the template, the profiles and the project name. Each has
// a default, and the flags answer them without a prompt. The generator
// copies the template, fills in the placeholders and prints how to run the
// project. It installs nothing.

import { readFile, readdir, mkdir, copyFile, writeFile, stat, access, chmod } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { dirname, join, resolve, basename } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
export const TEMPLATES_DIR = join(here, "templates");

export async function listTemplates(dir = TEMPLATES_DIR) {
  const names = (await readdir(dir, { withFileTypes: true }))
    .filter((d) => d.isDirectory() && !d.name.startsWith("_"))
    .map((d) => d.name)
    .sort();
  const out = [];
  for (const name of names) out.push(JSON.parse(await readFile(join(dir, name, "template.json"), "utf8")));
  return out;
}

export function parseArgs(argv) {
  const args = { name: null, template: null, profiles: null, yes: false, help: false, list: false, dir: null, human: null, agent: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const value = () => {
      const v = argv[++i];
      if (v === undefined || v.startsWith("-")) throw new Error(`${a} needs a value`);
      return v;
    };
    if (a === "--template" || a === "-t") args.template = value();
    else if (a === "--profiles" || a === "-p") args.profiles = value().split(",").map((s) => s.trim()).filter(Boolean);
    else if (a === "--yes" || a === "-y") args.yes = true;
    else if (a === "--help" || a === "-h") args.help = true;
    else if (a === "--list") args.list = true;
    else if (a === "--dir") args.dir = value();
    else if (a === "--human") args.human = uri(value());
    else if (a === "--agent") args.agent = uri(value());
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}`);
    else if (!args.name) args.name = a;
    else throw new Error(`Unexpected argument ${a}`);
  }
  return args;
}

const NAME_RE = /^[a-z][a-z0-9-]{0,63}$/;
// A participant URI: a scheme, a colon, then printable characters with no
// quote, backslash or whitespace, so it is safe inside the generated JSON.
const URI_RE = /^[a-z][a-z0-9+.-]*:[\x21\x23-\x5b\x5d-\x7e]+$/;

function uri(value) {
  if (!URI_RE.test(value)) throw new Error(`Not a participant URI: ${value}. Use the form human:name@example.org or agent:name.`);
  return value;
}

function slug(name) {
  return name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

/** Walk a directory, yielding relative file paths. */
async function walk(dir, prefix = "") {
  const out = [];
  for (const d of await readdir(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${d.name}` : d.name;
    if (d.isDirectory()) out.push(...(await walk(join(dir, d.name), rel)));
    else out.push(rel);
  }
  return out;
}

const TEXT = /\.(mjs|js|json|py|md|html|css|txt|csv|yml|yaml|toml|cfg|sh|gitignore)$|^\.gitignore$|(^|\/)(pre-commit|commit-msg|post-commit)$/;

/**
 * npm leaves .gitignore files out of a published package, so a template
 * ships its ignore list as `gitignore`, its Docker one as `dockerignore` and
 * its workflows under `github/`, and they are renamed here.
 */
export function dotted(rel) {
  if (/(^|\/)(gitignore|dockerignore)$/.test(rel)) return rel.replace(/(gitignore|dockerignore)$/, ".$1");
  if (rel.startsWith("github/")) return "." + rel;
  return rel;
}

/**
 * Generate a project. Returns the list of files written.
 *
 * `answers`: { name, template, profiles, human_uri, human_name, agent_uri }.
 * Placeholders replaced in text files: __PROJECT_NAME__, __WORKSPACE__,
 * __PROFILES_JSON__, __PROFILES_CSV__, __HUMAN_URI__, __HUMAN_NAME__,
 * __AGENT_URI__.
 */
export async function generate(answers, { templatesDir = TEMPLATES_DIR, targetDir } = {}) {
  const manifest = JSON.parse(await readFile(join(templatesDir, answers.template, "template.json"), "utf8"));
  const target = resolve(targetDir ?? answers.name);
  if (await exists(target)) {
    const entries = await readdir(target);
    if (entries.length) throw new Error(`${target} exists and is not empty.`);
  }
  const defaults = manifest.defaults ?? {};
  const fill = {
    __PROJECT_NAME__: answers.name,
    __WORKSPACE__: (answers.workspace ?? defaults.workspace ?? "wsp_{name}").replace("{name}", slug(answers.name)),
    __PROFILES_JSON__: JSON.stringify(answers.profiles),
    __PROFILES_CSV__: answers.profiles.join(","),
    __HUMAN_URI__: answers.human_uri ?? defaults.human_uri ?? "human:you@local",
    __HUMAN_NAME__: answers.human_name ?? defaults.human_name ?? "You",
    __AGENT_URI__: answers.agent_uri ?? defaults.agent_uri ?? "agent:drafter",
  };

  // Source files: the template's own, then the shared files it names.
  const sources = [];
  const own = join(templatesDir, answers.template);
  for (const rel of await walk(own)) {
    if (rel === "template.json") continue;
    sources.push({ from: join(own, rel), to: rel });
  }
  for (const [sharedPath, dest] of Object.entries(manifest.shared ?? {})) {
    const from = join(templatesDir, "_shared", sharedPath);
    if ((await stat(from)).isDirectory()) {
      for (const rel of await walk(from)) sources.push({ from: join(from, rel), to: `${dest}/${rel}` });
    } else {
      sources.push({ from, to: dest });
    }
  }

  const written = [];
  for (const { from, to: rel } of sources) {
    const to = dotted(rel);
    const dest = join(target, to);
    await mkdir(dirname(dest), { recursive: true });
    if (TEXT.test(to)) {
      let text = await readFile(from, "utf8");
      for (const [k, v] of Object.entries(fill)) text = text.split(k).join(v);
      await writeFile(dest, text);
    } else {
      await copyFile(from, dest);
    }
    // A git hook has to stay executable, so the template file's mode is kept.
    const mode = (await stat(from)).mode & 0o777;
    if (mode & 0o111) await chmod(dest, mode);
    written.push(to);
  }
  return { target, written, manifest, fill };
}

async function exists(p) {
  try { await access(p); return true; } catch { return false; }
}

function usage(templates) {
  const lines = [
    "Usage: npx create-chap-app <name> [--template <name>] [--profiles a,b] [--yes]",
    "",
    "Templates:",
    ...templates.map((t) => `  ${t.name.padEnd(20)} ${t.description}`),
    "",
    "Options:",
    "  --template, -t   the template (asked for when missing)",
    "  --profiles, -p   comma-separated profiles the workspace advertises (the template's default when missing)",
    "  --human          the reviewer's participant URI (default human:you@local)",
    "  --agent          the agent's participant URI (the template's default when missing)",
    "  --dir            where to write (default: ./<name>)",
    "  --yes, -y        take every default without asking",
    "  --list           print the templates and exit",
  ];
  return lines.join("\n");
}

async function ask(rl, question, fallback) {
  const answer = (await rl.question(`${question} [${fallback}] `)).trim();
  return answer || fallback;
}

export async function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const templates = await listTemplates();
  if (args.help) { console.log(usage(templates)); return 0; }
  if (args.list) { for (const t of templates) console.log(`${t.name.padEnd(20)} ${t.description}`); return 0; }

  const interactive = !args.yes && stdin.isTTY;
  const rl = interactive ? createInterface({ input: stdin, output: stdout }) : null;
  try {
    let template = args.template;
    if (!template) {
      if (!rl) template = templates[0].name;
      else {
        console.log("Which template?");
        templates.forEach((t, i) => console.log(`  ${i + 1}. ${t.name.padEnd(20)} ${t.description}`));
        const pick = await ask(rl, "Template (number or name)", "1");
        template = /^\d+$/.test(pick) ? templates[Number(pick) - 1]?.name : pick;
      }
    }
    const manifest = templates.find((t) => t.name === template);
    if (!manifest) throw new Error(`No template named ${template}. Templates: ${templates.map((t) => t.name).join(", ")}`);

    let profiles = args.profiles;
    if (!profiles) {
      if (!rl) profiles = manifest.default_profiles;
      else {
        console.log(`Profiles the workspace advertises. Choices: ${manifest.profile_choices.join(", ")}`);
        profiles = (await ask(rl, "Profiles (comma separated)", manifest.default_profiles.join(","))).split(",").map((s) => s.trim()).filter(Boolean);
      }
    }
    const unknown = profiles.filter((p) => !manifest.profile_choices.includes(p));
    if (unknown.length) throw new Error(`Unknown profile ${unknown.join(", ")}. Choices: ${manifest.profile_choices.join(", ")}`);
    if (!profiles.includes("core/1.0")) profiles = ["core/1.0", ...profiles];

    let name = args.name;
    if (!name) {
      if (!rl) name = `my-${template}`;
      else name = await ask(rl, "Project name", `my-${template}`);
    }
    if (!NAME_RE.test(name)) throw new Error(`The project name must be lower case letters, digits and hyphens, starting with a letter: ${name}`);

    const result = await generate(
      { name, template, profiles, human_uri: args.human ?? undefined, agent_uri: args.agent ?? undefined },
      { targetDir: args.dir ?? undefined },
    );
    console.log(`\nCreated ${result.target} from the ${manifest.title} template with ${profiles.join(", ")}.\n`);
    console.log("Next:");
    console.log(`  cd ${args.dir ? result.target : basename(result.target)}`);
    for (const step of manifest.run ?? []) console.log(`  ${step}`);
    console.log("\nREADME.md says what each profile changes in this project and how to attach your own agent and data.");
    return 0;
  } finally {
    rl?.close();
  }
}

function runDirectly() {
  try { return !!process.argv[1] && realpathSync(fileURLToPath(import.meta.url)) === realpathSync(process.argv[1]); } catch { return false; }
}

if (runDirectly()) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e.message); process.exit(1); });
}
