// The gate's hooks in a repository that has hooks of its own: they run
// after the gate's check, a hook the gate has none of runs as before, and
// removing the gate's hooks puts back what was there. A husky v9 setup
// (core.hooksPath at .husky/_, whose scripts run .husky/<name>) and plain
// .git/hooks are the two shapes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { installHooks } from "../index.mjs";
import { git } from "../lib/git.mjs";
import { hookScript } from "../lib/install-hooks.mjs";
import { gateProject } from "./helpers.mjs";

const run = promisify(execFile);
const quiet = () => {};
const exists = (p) => stat(p).then(() => true, () => false);

async function repository() {
  const repo = await mkdtemp(join(tmpdir(), "chap-hooked-"));
  await git(repo, ["init", "-q", "-b", "main"]);
  await git(repo, ["config", "user.email", "a@example.org"]);
  await git(repo, ["config", "user.name", "A"]);
  await writeFile(join(repo, "f.txt"), "one\n");
  await git(repo, ["add", "f.txt"]);
  await git(repo, ["-c", "core.hooksPath=/dev/null", "commit", "-qm", "base"]);
  return repo;
}

/** husky v9 as it installs itself: core.hooksPath .husky/_, a runner, and the project's own .husky/<name>. */
async function withHusky(repo) {
  await mkdir(join(repo, ".husky", "_"), { recursive: true });
  await writeFile(join(repo, ".husky", "_", "h"), '#!/usr/bin/env sh\nn=$(basename "$0")\ns=$(dirname "$(dirname "$0")")/$n\n[ ! -f "$s" ] && exit 0\nsh -e "$s" "$@"\n', { mode: 0o755 });
  for (const name of ["pre-commit", "commit-msg", "post-checkout", "pre-push"]) await writeFile(join(repo, ".husky", "_", name), '#!/usr/bin/env sh\n. "$(dirname "$0")/h"\n', { mode: 0o755 });
  await writeFile(join(repo, ".husky", "pre-commit"), "echo husky-pre-commit >> \"$(git rev-parse --git-dir)/ran\"\n");
  await writeFile(join(repo, ".husky", "post-checkout"), "echo husky-post-checkout >> \"$(git rev-parse --git-dir)/ran\"\n");
  await writeFile(join(repo, ".husky", "pre-push"), "cat >> \"$(git rev-parse --git-dir)/pushed\"\n");
  await git(repo, ["config", "core.hooksPath", ".husky/_"]);
}

const ran = async (repo) => (await readFile(join(repo, ".git", "ran"), "utf8").catch(() => "")).split("\n").filter(Boolean);
const commit = (repo, env = {}) => run("git", ["commit", "-qam", "change"], { cwd: repo, env: { ...process.env, CHAP_AUTOSTART: "0", ...env } });

test("in a husky repository the gate's hooks run first and husky's after them, and removing them puts husky back", async () => {
  const { dir: gateDir } = await gateProject({ port: 1 });
  const repo = await repository();
  await withHusky(repo);
  const lines = [];
  const done = await installHooks(repo, { gateDir, log: (l) => lines.push(l) });
  assert.equal((await git(repo, ["config", "--get", "core.hooksPath"])).trim(), done.hooksPath);
  assert.ok(lines.some((l) => /run after the gate's: .*pre-commit/.test(l)));
  assert.ok(lines.some((l) => /husky sets core\.hooksPath again/.test(l)));

  // The gate is down: its check refuses, and husky's hook never runs.
  await writeFile(join(repo, "f.txt"), "one\ntwo\n");
  await assert.rejects(commit(repo), (e) => /chap: .*not answering/.test(e.stderr));
  assert.deepEqual(await ran(repo), []);
  // Let through by the gate (CHAP_GATE=off), husky's hook runs after it.
  await commit(repo, { CHAP_GATE: "off" });
  assert.deepEqual(await ran(repo), ["husky-pre-commit"]);
  // A hook the gate has no check in runs as it did.
  await git(repo, ["checkout", "-q", "-b", "other"]);
  assert.deepEqual(await ran(repo), ["husky-pre-commit", "husky-post-checkout"]);

  await installHooks(repo, { gateDir, remove: true, log: quiet });
  assert.equal((await git(repo, ["config", "--get", "core.hooksPath"])).trim(), ".husky/_", "husky's setting is back");
  assert.equal(await exists(done.hooksPath), false);
  await assert.rejects(git(repo, ["config", "--get", "chap.chainTo"]));
  await writeFile(join(repo, "f.txt"), "three\n");
  await commit(repo);
  assert.deepEqual(await ran(repo), ["husky-pre-commit", "husky-post-checkout", "husky-pre-commit"], "husky alone runs again");
});

test("a push's refs reach the gate's pre-push hook and the one the repository ran before", async () => {
  const { dir: gateDir } = await gateProject({ port: 1 });
  const repo = await repository();
  await withHusky(repo);
  const remote = await mkdtemp(join(tmpdir(), "chap-remote-"));
  await git(remote, ["init", "-q", "--bare"]);
  await git(repo, ["remote", "add", "origin", remote]);
  await installHooks(repo, { gateDir, log: quiet });
  await run("git", ["push", "-q", "origin", "main"], { cwd: repo, env: { ...process.env, CHAP_GATE: "off" } });
  const head = (await git(repo, ["rev-parse", "HEAD"])).trim();
  assert.match(await readFile(join(repo, ".git", "pushed"), "utf8"), new RegExp(`^refs/heads/main ${head} refs/heads/main 0{40}$`, "m"));
});

test("hooks in .git/hooks run after the gate's, and removing the gate's hooks leaves core.hooksPath unset", async () => {
  const { dir: gateDir } = await gateProject({ port: 1 });
  const repo = await repository();
  await writeFile(join(repo, ".git", "hooks", "pre-commit"), "#!/bin/sh\necho own-pre-commit >> \"$(git rev-parse --git-dir)/ran\"\n", { mode: 0o755 });
  await installHooks(repo, { gateDir, log: quiet });
  await writeFile(join(repo, "f.txt"), "two\n");
  await commit(repo, { CHAP_GATE: "off" });
  assert.deepEqual(await ran(repo), ["own-pre-commit"]);
  await installHooks(repo, { gateDir, remove: true, log: quiet });
  await assert.rejects(git(repo, ["config", "--get", "core.hooksPath"]), "nothing was set before, and nothing is now");
});

test("installing again keeps what the repository ran before", async () => {
  const { dir: gateDir } = await gateProject({ port: 1 });
  const repo = await repository();
  await withHusky(repo);
  await installHooks(repo, { gateDir, log: quiet });
  await installHooks(repo, { gateDir, log: quiet });
  await installHooks(repo, { gateDir, remove: true, log: quiet });
  assert.equal((await git(repo, ["config", "--get", "core.hooksPath"])).trim(), ".husky/_");
});

test("a hook that cannot find the gate's package refuses the commit and says why", async () => {
  const dir = await mkdtemp(join(tmpdir(), "chap-shim-"));
  const path = join(dir, "pre-commit");
  await writeFile(path, hookScript("pre-commit", { cli: join(dir, "missing", "chap-code-gate.mjs"), gateDir: "/the/gate" }));
  await chmod(path, 0o755);
  await assert.rejects(run(path, []), (e) => e.code === 1 && /chap: the code gate is not installed at .*missing.*run npm install in \/the\/gate/.test(e.stderr));
});

test("run from a prepare script, the install says nothing of prepare, and outside a repository installs nothing", async () => {
  const { dir: gateDir } = await gateProject({ port: 1 });
  const repo = await repository();
  await withHusky(repo);
  await writeFile(join(repo, "package.json"), JSON.stringify({ scripts: { prepare: "husky && chap-code-gate install-hooks --gate chap --if-repo" } }));
  const lines = [];
  await installHooks(repo, { gateDir, log: (l) => lines.push(l) });
  assert.ok(!lines.some((l) => /husky sets core\.hooksPath again/.test(l)), "prepare already runs the install");

  const bin = new URL("../bin/chap-code-gate.mjs", import.meta.url).pathname;
  const outside = await mkdtemp(join(tmpdir(), "chap-no-repo-"));
  const { stdout } = await run(process.execPath, [bin, "install-hooks", "--gate", gateDir, "--if-repo"], { cwd: outside });
  assert.match(stdout, /is not inside a git repository, so no hooks were installed/);
  await assert.rejects(run(process.execPath, [bin, "install-hooks", "--gate", gateDir], { cwd: outside }), /is not inside a git repository/);
});

test("a gate named by a relative path is written into the hooks by its full path", async () => {
  const repo = await repository();
  const { dir: gateDir } = await gateProject({ port: 1 });
  const { cp } = await import("node:fs/promises");
  await cp(gateDir, join(repo, "chap"), { recursive: true });
  const bin = new URL("../bin/chap-code-gate.mjs", import.meta.url).pathname;
  await run(process.execPath, [bin, "install-hooks", "--gate", "chap"], { cwd: repo });
  const hooksPath = (await git(repo, ["config", "--get", "core.hooksPath"])).trim();
  const script = await readFile(join(hooksPath, "pre-commit"), "utf8");
  assert.ok(script.includes(`--gate '${join(repo, "chap")}'`), script);
});
