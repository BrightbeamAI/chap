// add: CHAP goes into a project that exists, in a directory of its own,
// with its dependencies and scripts merged into the project's, and for the
// code gate, hooks that run the repository's own after the gate's check,
// husky's included, which survive husky's prepare.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { VERSION, listTemplates } from "../index.mjs";
import { contentHash } from "../lib/released.mjs";
import { runAdd } from "../lib/add.mjs";

const run = promisify(execFile);
const GATE_BIN = fileURLToPath(new URL("../../chap-code-gate/bin/chap-code-gate.mjs", import.meta.url));
const nobody = () => ({ name: "Rev Iewer", email: "rev@example.org" });

async function add(argv, options = {}) {
  const lines = [];
  const code = await runAdd(argv, { say: (t) => lines.push(t), identity: nobody, ...options });
  return { code, text: lines.join("\n") };
}

/** A Node.js project of someone's own, in a git repository. */
async function nodeProject(pkg = {}) {
  const dir = await mkdtemp(join(tmpdir(), "cca-add-"));
  await writeFile(join(dir, "package.json"), `${JSON.stringify({ name: "existing-app", version: "1.0.0", scripts: { test: "node --test" }, dependencies: { express: "^5.2.1" }, ...pkg }, null, 2)}\n`);
  await writeFile(join(dir, "server.mjs"), "export const theirs = true;\n");
  await writeFile(join(dir, "README.md"), "# existing-app\n");
  await writeFile(join(dir, ".gitignore"), "node_modules/\n");
  for (const args of [["init", "-q", "-b", "main"], ["config", "user.email", "a@example.org"], ["config", "user.name", "A"]]) await run("git", args, { cwd: dir });
  return dir;
}

/** husky v9 as its prepare leaves a repository: core.hooksPath .husky/_, a runner, and the project's .husky/pre-commit. */
async function huskyPrepare(dir) {
  await mkdir(join(dir, ".husky", "_"), { recursive: true });
  await writeFile(join(dir, ".husky", "_", "h"), '#!/usr/bin/env sh\nn=$(basename "$0")\ns=$(dirname "$(dirname "$0")")/$n\n[ ! -f "$s" ] && exit 0\nsh -e "$s" "$@"\n', { mode: 0o755 });
  await writeFile(join(dir, ".husky", "_", "pre-commit"), '#!/usr/bin/env sh\n. "$(dirname "$0")/h"\n', { mode: 0o755 });
  await writeFile(join(dir, ".husky", "pre-commit"), "echo husky >> \"$(git rev-parse --git-dir)/ran\"\n");
  await run("git", ["config", "core.hooksPath", ".husky/_"], { cwd: dir });
}

test("the code gate goes into a Node.js project in chap/, beside the project's own files", async () => {
  const dir = await nodeProject();
  const before = { server: await readFile(join(dir, "server.mjs"), "utf8"), readme: await readFile(join(dir, "README.md"), "utf8") };
  const { text } = await add([dir]);
  const gate = (await listTemplates()).find((t) => t.name === "code-gate");
  assert.deepEqual((await readdir(join(dir, "chap"))).sort(), [...gate.add.files].sort());
  assert.equal(await readFile(join(dir, "server.mjs"), "utf8"), before.server);
  assert.equal(await readFile(join(dir, "README.md"), "utf8"), before.readme);
  const pkg = JSON.parse(await readFile(join(dir, "package.json"), "utf8"));
  assert.equal(pkg.dependencies.express, "^5.2.1");
  assert.equal(pkg.dependencies["@brightbeamai/chap-code-gate"], `^${VERSION}`);
  assert.equal(pkg.dependencies["@brightbeamai/chap-server"], `^${VERSION}`);
  assert.equal(pkg.scripts.chap, "node chap/server.mjs");
  assert.equal(pkg.scripts["chap:hooks"], "chap-code-gate install-hooks --gate chap");
  assert.equal(pkg.scripts.prepare, undefined, "no husky, so prepare is left alone");
  assert.equal(await readFile(join(dir, ".gitignore"), "utf8"), "node_modules/\n\n# CHAP: what chap/ writes as it runs\nchap/data/\nchap/keys/\nchap/analytics/\n");
  const config = JSON.parse(await readFile(join(dir, "chap", "chap.config.json"), "utf8"));
  assert.deepEqual(config.template, { name: "code-gate", version: VERSION });
  assert.ok((await readFile(join(dir, "chap", "AGENT_INSTRUCTIONS.md"), "utf8")).includes(join(dir, "chap", "propose.mjs")), "the instructions name the gate's propose.mjs");
  assert.match(text, /Next:\n {2}npm install\n {2}npm run chap:hooks\n {2}npm run chap\n/);
  await assert.rejects(add([dir]), /chap exists and is not empty; name another directory with --into/);
});

test("with husky, npm install keeps the gate's hooks, and husky's hook runs after the gate's check", async () => {
  const dir = await nodeProject({ scripts: { test: "node --test", prepare: "husky" }, devDependencies: { husky: "^9.1.7" } });
  const { text } = await add([dir]);
  const pkg = JSON.parse(await readFile(join(dir, "package.json"), "utf8"));
  assert.equal(pkg.scripts.prepare, "husky && chap-code-gate install-hooks --gate chap --if-repo");
  assert.match(text, /Next:\n {2}npm install\n {2}npm run chap\n/, "npm install installs the hooks");
  // The gate listens where nothing answers, so its check refuses unless switched off.
  const configPath = join(dir, "chap", "chap.config.json");
  await writeFile(configPath, (await readFile(configPath, "utf8")).replace(/"port": \d+/, '"port": 9'));

  // npm install runs prepare: husky points core.hooksPath at .husky/_, then the gate's install.
  await huskyPrepare(dir);
  await run(process.execPath, [GATE_BIN, "install-hooks", "--gate", "chap", "--if-repo"], { cwd: dir });
  assert.equal((await run("git", ["config", "--get", "chap.chainTo"], { cwd: dir })).stdout.trim(), join(dir, ".husky", "_"));

  await run("git", ["add", "server.mjs"], { cwd: dir });
  const commit = (env) => run("git", ["commit", "-qm", "a change"], { cwd: dir, env: { ...process.env, CHAP_AUTOSTART: "0", ...env } });
  await assert.rejects(commit({}), (e) => /chap: The gate at .* is not answering/.test(e.stderr));
  await commit({ CHAP_GATE: "off" });
  assert.equal(await readFile(join(dir, ".git", "ran"), "utf8"), "husky\n");

  // Taken out, the hooks husky set up are back.
  await run(process.execPath, [GATE_BIN, "install-hooks", "--gate", "chap", "--remove"], { cwd: dir });
  assert.equal((await run("git", ["config", "--get", "core.hooksPath"], { cwd: dir })).stdout.trim(), ".husky/_");
});

test("the MCP gate adds its server and leaves the hooks and prepare alone", async () => {
  const dir = await nodeProject({ scripts: { prepare: "husky" } });
  await add([dir, "--template", "mcp-gate", "--into", "tools/chap"]);
  const pkg = JSON.parse(await readFile(join(dir, "package.json"), "utf8"));
  assert.deepEqual(pkg.scripts, { prepare: "husky", chap: "node tools/chap/server.mjs" });
  assert.deepEqual((await readdir(join(dir, "tools", "chap"))).sort(), ["AGENT_INSTRUCTIONS.md", "chap.config.json", "server.mjs"]);
});

test("a Python project gets the template's chap- pins in its requirements, or is told them", async () => {
  // A Python template with an add section, as the Python templates can declare one.
  const templates = await mkdtemp(join(tmpdir(), "cca-py-templates-"));
  await mkdir(join(templates, "py-desk"), { recursive: true });
  await writeFile(join(templates, "py-desk", "template.json"), JSON.stringify({
    name: "py-desk", title: "Desk", language: "python", description: "A desk.", default_profiles: ["core/1.0", "review/1.0"], profile_choices: ["core/1.0", "review/1.0"],
    add: { dir: "chap", files: ["chap.config.json", "desk.py"], run: ["python {dir}/desk.py"], gitignore: ["{dir}/data/"] },
  }));
  await writeFile(join(templates, "py-desk", "chap.config.json"), '{\n  "template": { "name": "__TEMPLATE_NAME__", "version": "__TEMPLATE_VERSION__" },\n  "workspace": "__WORKSPACE__",\n  "profiles": __PROFILES_JSON__,\n  "port": 8788\n}\n');
  await writeFile(join(templates, "py-desk", "desk.py"), "print('desk')\n");
  await writeFile(join(templates, "py-desk", "requirements.txt"), "chap-coordinator[crypto]>=0.3.4,<0.4\n");

  const dir = await mkdtemp(join(tmpdir(), "cca-add-py-"));
  await writeFile(join(dir, "requirements.txt"), "flask>=3\nchap-coordinator>=0.3.1\n");
  const { text } = await add([dir], { templatesDir: templates });
  assert.equal(await readFile(join(dir, "requirements.txt"), "utf8"), "flask>=3\nchap-coordinator[crypto]>=0.3.4,<0.4\n");
  assert.equal(await readFile(join(dir, "chap", "desk.py"), "utf8"), "print('desk')\n");
  assert.match(text, /Next:\n {2}python -m pip install -r requirements\.txt\n {2}python chap\/desk\.py\n/);

  const pyproject = await mkdtemp(join(tmpdir(), "cca-add-pyproject-"));
  await writeFile(join(pyproject, "pyproject.toml"), "[project]\nname = \"x\"\n");
  const told = await add([pyproject], { templatesDir: templates });
  assert.match(told.text, /no requirements\.txt: add these to its dependencies by hand:\n {2}chap-coordinator\[crypto\]>=0\.3\.4,<0\.4/);
});

test("add refuses what it cannot add to, and --dry-run changes nothing", async () => {
  const empty = await mkdtemp(join(tmpdir(), "cca-add-none-"));
  await assert.rejects(add([empty]), /has no package\.json, requirements\.txt or pyproject\.toml/);
  const dir = await nodeProject();
  await assert.rejects(add([dir, "--template", "production"]), /The production template is a project of its own/);
  await assert.rejects(add([dir, "--template", "support-desk"]), /support-desk template is a project of its own|runs on Python/);
  await assert.rejects(add([dir, "--into", "../elsewhere"]), /--into takes a directory inside the project/);
  const files = async () => Object.fromEntries(await Promise.all((await readdir(dir)).filter((f) => f !== ".git").map(async (f) => [f, (await stat(join(dir, f))).isFile() ? contentHash(await readFile(join(dir, f))) : "dir"])));
  const before = await files();
  const { text } = await add([dir, "--dry-run"]);
  assert.match(text, /^To add the code-gate template/);
  assert.match(text, /Nothing was changed: --dry-run\./);
  assert.deepEqual(await files(), before);
});
