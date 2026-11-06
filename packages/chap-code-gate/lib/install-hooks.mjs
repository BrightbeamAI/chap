// Put a repository under the gate: `chap-code-gate install-hooks [--repo <path>] [--remove]`.
//
// Writes the repository's own hooks directory, chap/hooks in its git
// directory, and points core.hooksPath at it, so every commit there runs the
// gate's pre-commit, commit-msg and post-commit hooks, and every push its
// pre-push hook. Each runs the gate's check for the gate project, then the
// hook of the same name the repository ran before: a husky or lefthook
// setup, a core.hooksPath of its own, or .git/hooks. A hook the repository
// ran before under another name runs as it did. --remove takes the gate's
// hooks out and puts back the core.hooksPath that was there.
//
// A hook fails closed: one that cannot find the gate's package, or node,
// refuses the commit and says why.
//
// When the agent's key is in the gate project's keys/, the repository's
// gpg.ssh.allowedSignersFile is pointed at keys/allowed_signers, unless it
// names a file already, so `git log --show-signature` there says which
// commits the agent's key signed.
//
// --claude also registers claude-session.mjs as a Claude Code SessionStart
// hook in the repository's .claude/settings.local.json, kept out of git, so
// the commands Claude Code runs there find the session's model in
// CHAP_MODEL and the commits name it.

import { access, appendFile, chmod, mkdir, readdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadGate } from "./gate.mjs";
import { git, repoRoot } from "./git.mjs";

/** The hooks the gate runs its own check in. */
export const GATE_HOOKS = ["pre-commit", "commit-msg", "post-commit", "pre-push"];

/** The gate project's own hooks/, which repositories hooked up before 0.3.5 point at. */
export const hooksDir = (gateDir) => join(gateDir, "hooks");

/** The command Claude Code runs as each session starts, to hand its model to the gate. */
export const claudeSessionHook = (gateDir) => `node ${JSON.stringify(join(gateDir, "claude-session.mjs"))}`;

/** This package's command, which the hooks run, by its real path. */
const CLI = realpathSync(fileURLToPath(new URL("../bin/chap-code-gate.mjs", import.meta.url)));

/** review_at from the configuration the hooks will read. */
async function reviewAtOf(gateDir) {
  try { return (await loadGate(gateDir)).config.review_at ?? "commit"; } catch { return "commit"; }
}

/** A string as a POSIX shell reads it back unchanged: in single quotes. */
const sq = (s) => `'${String(s).replace(/'/g, "'\\''")}'`;

/**
 * The hook script for `stage` in a repository: the gate's check for the
 * project at `gateDir`, through the package's command at `cli`, then the
 * hook of that name in `chainTo` when there is one. pre-push's input, the
 * refs being pushed, goes to both.
 */
export function hookScript(stage, { cli = CLI, gateDir, chainTo = null }) {
  const previous = chainTo ? join(chainTo, stage) : null;
  const lines = [
    "#!/bin/sh",
    `# The CHAP code gate's ${stage} hook, written by chap-code-gate install-hooks for`,
    `# the gate project at ${gateDir}. It runs the gate's check${previous ? `, then the ${stage}` : ""}`,
    ...(previous ? ["# hook this repository ran before."] : []),
    "# chap-code-gate install-hooks --remove takes it out.",
    `cli=${sq(cli)}`,
    `if [ ! -f "$cli" ]; then echo "chap: the code gate is not installed at $cli: run npm install in ${gateDir.replace(/["\\$`]/g, "")}, then install-hooks again. CHAP_GATE=off commits without the check." >&2; exit 1; fi`,
  ];
  if (stage === "pre-push") {
    lines.push("input=$(cat)", `printf '%s\\n' "$input" | node "$cli" hook ${stage} --gate ${sq(gateDir)} "$@" || exit $?`);
    if (previous) lines.push(`if [ -x ${sq(previous)} ]; then printf '%s\\n' "$input" | ${sq(previous)} "$@" || exit $?; fi`);
  } else {
    lines.push(`node "$cli" hook ${stage} --gate ${sq(gateDir)} "$@" || exit $?`);
    if (previous) lines.push(`if [ -x ${sq(previous)} ]; then exec ${sq(previous)} "$@"; fi`);
  }
  return lines.join("\n") + "\n";
}

/** The script for a hook the gate has no check in: the one the repository ran before, as it ran it. */
const passThrough = (stage, previous) => `#!/bin/sh\n# Written by chap-code-gate install-hooks: the ${stage} hook this repository ran before.\nexec ${sq(previous)} "$@"\n`;

async function configValue(repo, key, scope = []) {
  try { return (await git(repo, ["config", ...scope, "--get", key])).trim() || null; } catch { return null; }
}

/** The hooks in a directory a repository ran: executable files, samples left out. */
async function hooksIn(dir) {
  let names = [];
  try { names = await readdir(dir); } catch { return []; }
  const out = [];
  for (const name of names) {
    if (name.endsWith(".sample") || name.startsWith(".")) continue;
    try { const st = await stat(join(dir, name)); if (st.isFile() && st.mode & 0o111) out.push(name); } catch { /* gone */ }
  }
  return out;
}

export async function installHooks(repoPath, { remove = false, log = console.log, gateDir } = {}) {
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${resolve(repoPath)} is not inside a git repository`);
  const common = resolve(repo, (await git(repo, ["rev-parse", "--git-common-dir"])).trim());
  const ours = join(common, "chap", "hooks");
  const local = await configValue(repo, "core.hooksPath", ["--local"]);
  if (remove) {
    const restore = await configValue(repo, "chap.restoreHooksPath", ["--local"]);
    if (restore) await git(repo, ["config", "--local", "core.hooksPath", restore]);
    else if (local) await git(repo, ["config", "--local", "--unset", "core.hooksPath"]);
    for (const key of ["chap.chainTo", "chap.restoreHooksPath"]) { try { await git(repo, ["config", "--local", "--unset", key]); } catch { /* not set */ } }
    await rm(ours, { recursive: true, force: true });
    log(restore ? `The gate's hooks are out of ${repo}; core.hooksPath is ${restore} again` : `The gate's hooks are out of ${repo}; it runs the hooks in ${join(common, "hooks")} again`);
    return { repo, installed: false };
  }
  // What the repository ran before: kept from an earlier install, or read now.
  const reinstall = !!local && resolve(repo, local) === ours;
  const before = reinstall ? null : local && resolve(repo, local) !== resolve(hooksDir(gateDir)) ? local : null;
  let chainTo = reinstall ? await configValue(repo, "chap.chainTo", ["--local"]) : null;
  if (!reinstall) {
    const effective = before ?? (local ? null : await configValue(repo, "core.hooksPath"));
    chainTo = effective ? resolve(repo, effective) : join(common, "hooks");
  }
  await rm(ours, { recursive: true, force: true });
  await mkdir(ours, { recursive: true });
  const theirs = chainTo ? await hooksIn(chainTo) : [];
  for (const stage of GATE_HOOKS) await writeFile(join(ours, stage), hookScript(stage, { gateDir, chainTo }), { mode: 0o755 });
  for (const stage of theirs.filter((n) => !GATE_HOOKS.includes(n))) await writeFile(join(ours, stage), passThrough(stage, join(chainTo, stage)), { mode: 0o755 });
  if (!reinstall) {
    if (before) await git(repo, ["config", "--local", "chap.restoreHooksPath", before]);
    if (chainTo) await git(repo, ["config", "--local", "chap.chainTo", chainTo]);
  }
  await git(repo, ["config", "--local", "core.hooksPath", ours]);
  await access(join(ours, "pre-commit"));
  log(`core.hooksPath in ${repo} now points at ${ours}; ${(await reviewAtOf(gateDir)) === "push" ? "a branch is reviewed before it is pushed, and every push is checked" : "every commit there needs an approved change, and every push is checked"}`);
  if (theirs.length) log(`The hooks it ran before, in ${chainTo}, run after the gate's: ${theirs.join(", ")}`);
  if (before && /(^|\/)\.husky(\/_)?\/?$/.test(before)) log("husky sets core.hooksPath again whenever its prepare script runs, on npm install: run install-hooks after it, as in \"prepare\": \"husky && chap-code-gate install-hooks\"");
  const signers = join(gateDir, "keys", "allowed_signers");
  const signersSet = await configValue(repo, "gpg.ssh.allowedSignersFile");
  try {
    await access(signers);
    if (!signersSet) {
      await git(repo, ["config", "gpg.ssh.allowedSignersFile", signers]);
      log(`gpg.ssh.allowedSignersFile in ${repo} now names ${signers}, so git log --show-signature shows the agent's signatures`);
    }
  } catch { /* no agent key yet: npm run keys writes it */ }
  return { repo, installed: true, hooksPath: ours, chainTo };
}

/**
 * Register claude-session.mjs as a Claude Code SessionStart hook in a
 * repository's .claude/settings.local.json, merged with what the file holds,
 * and keep that file out of git (it names a path on this machine) through
 * the repository's info/exclude. Running it again changes nothing.
 */
export async function installClaudeHook(repoPath, { log = console.log, gateDir } = {}) {
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${resolve(repoPath)} is not inside a git repository`);
  const CLAUDE_SESSION_HOOK = claudeSessionHook(gateDir);
  const path = join(repo, ".claude", "settings.local.json");
  const byHand = `add to its "hooks" a SessionStart entry: { "hooks": [{ "type": "command", "command": ${JSON.stringify(CLAUDE_SESSION_HOOK)} }] }`;
  let settings = {};
  try { settings = JSON.parse(await readFile(path, "utf8")); } catch (e) {
    if (e.code !== "ENOENT") throw new Error(`${path} does not read as JSON (${e.message}). Correct it, or ${byHand}.`);
  }
  const isObject = (v) => v && typeof v === "object" && !Array.isArray(v);
  if (!isObject(settings)) throw new Error(`${path} holds no settings object; ${byHand}.`);
  settings.hooks ??= {};
  if (!isObject(settings.hooks)) throw new Error(`"hooks" in ${path} is not an object; ${byHand}.`);
  settings.hooks.SessionStart ??= [];
  const groups = settings.hooks.SessionStart;
  if (!Array.isArray(groups)) throw new Error(`"hooks.SessionStart" in ${path} is not a list; ${byHand}.`);
  const present = groups.some((g) => Array.isArray(g?.hooks) && g.hooks.some((h) => h?.command === CLAUDE_SESSION_HOOK));
  if (!present) {
    groups.push({ hooks: [{ type: "command", command: CLAUDE_SESSION_HOOK }] });
    await mkdir(dirname(path), { recursive: true });
    await writeFile(path, `${JSON.stringify(settings, null, 2)}\n`);
  }
  const exclude = resolve(repo, (await git(repo, ["rev-parse", "--git-path", "info/exclude"])).trim());
  const listed = await readFile(exclude, "utf8").catch(() => "");
  if (!listed.split("\n").includes("/.claude/settings.local.json")) {
    await mkdir(dirname(exclude), { recursive: true });
    await appendFile(exclude, `${listed && !listed.endsWith("\n") ? "\n" : ""}/.claude/settings.local.json\n`);
  }
  log(present
    ? `${path} runs the gate's SessionStart hook already`
    : `Claude Code hands each session's model to the gate in ${repo}: ${path} runs ${join(gateDir, "claude-session.mjs")} as each session starts, from the next session on`);
  return { path, added: !present };
}

export async function main(argv, { gateDir, log = console.log } = {}) {
  const at = argv.indexOf("--repo");
  const repo = at >= 0 ? argv[at + 1] : process.cwd();
  const done = await installHooks(repo, { remove: argv.includes("--remove"), log, gateDir });
  if (argv.includes("--claude") && done.installed) await installClaudeHook(repo, { log, gateDir });
  return 0;
}
