// Put a repository under the gate: `node install-hooks.mjs [--repo <path>] [--remove]`.
//
// Sets core.hooksPath in the repository to this project's hooks/ directory,
// so every commit there runs the gate's pre-commit, commit-msg and
// post-commit hooks, and every push its pre-push hook. A repository that
// already has a hooks path, or hooks
// of its own in .git/hooks, is reported and left as it is unless --force
// is given. --remove takes the setting away again.
//
// When the agent's key is in keys/, the repository's
// gpg.ssh.allowedSignersFile is pointed at keys/allowed_signers, unless it
// names a file already, so `git log --show-signature` there says which
// commits the agent's key signed.

import { access, chmod, readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadGate } from "./lib/gate.mjs";
import { git, repoRoot } from "./lib/git.mjs";

const here = dirname(fileURLToPath(import.meta.url));
export const HOOKS_DIR = join(here, "hooks");

/** review_at from the configuration the hooks will read. */
async function reviewAtHere() {
  try { return (await loadGate(here)).config.review_at ?? "commit"; } catch { return "commit"; }
}

export async function installHooks(repoPath, { force = false, remove = false, log = console.log } = {}) {
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${resolve(repoPath)} is not inside a git repository`);
  if (remove) {
    try { await git(repo, ["config", "--unset", "core.hooksPath"]); } catch { /* was not set */ }
    log(`core.hooksPath removed from ${repo}; commits there are no longer gated`);
    return { repo, installed: false };
  }
  for (const name of ["pre-commit", "commit-msg", "post-commit", "pre-push"]) await chmod(join(HOOKS_DIR, name), 0o755);
  let current = "";
  try { current = (await git(repo, ["config", "--get", "core.hooksPath"])).trim(); } catch { /* not set */ }
  if (current && resolve(repo, current) !== HOOKS_DIR && !force) {
    throw new Error(`${repo} already runs hooks from ${current}. Pass --force to point it at the gate, or chain them yourself.`);
  }
  const own = join(repo, ".git", "hooks");
  let theirs = [];
  try { theirs = (await readdir(own)).filter((n) => !n.endsWith(".sample")); } catch { /* no hooks dir */ }
  if (theirs.length && !force) {
    throw new Error(`${own} holds hooks of its own (${theirs.join(", ")}), which core.hooksPath would bypass. Pass --force to proceed.`);
  }
  await git(repo, ["config", "core.hooksPath", HOOKS_DIR]);
  await access(join(HOOKS_DIR, "pre-commit"));
  log(`core.hooksPath in ${repo} now points at ${HOOKS_DIR}; ${(await reviewAtHere()) === "push" ? "a branch is reviewed before it is pushed, and every push is checked" : "every commit there needs an approved change, and every push is checked"}`);
  const signers = join(here, "keys", "allowed_signers");
  let current_ = "";
  try { current_ = (await git(repo, ["config", "--get", "gpg.ssh.allowedSignersFile"])).trim(); } catch { /* not set */ }
  try {
    await access(signers);
    if (!current_) {
      await git(repo, ["config", "gpg.ssh.allowedSignersFile", signers]);
      log(`gpg.ssh.allowedSignersFile in ${repo} now names ${signers}, so git log --show-signature shows the agent's signatures`);
    }
  } catch { /* no agent key yet: npm run keys writes it */ }
  return { repo, installed: true };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const argv = process.argv.slice(2);
  const at = argv.indexOf("--repo");
  const repo = at >= 0 ? argv[at + 1] : process.cwd();
  installHooks(repo, { force: argv.includes("--force"), remove: argv.includes("--remove") }).catch((e) => {
    console.error(e.message);
    process.exit(1);
  });
}
