// Git, as the gate uses it: a patch of the working tree or the index, the
// tree a patch produces, commits with trailers, and notes beside commits.
//
// Every call runs the git on PATH in the repository given. The index is
// never touched except by `commit`: a patch is taken through a temporary
// index, and so is the tree a patch produces. Inside a hook, git sets
// GIT_INDEX_FILE to the index the commit is being made from, and the
// functions that read the staged change honour it.

import { execFile } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const MAX = 64 * 1024 * 1024;

/** Run git in `repo`. Returns stdout. A failure throws with git's message. */
export async function git(repo, args, { env = {}, input } = {}) {
  try {
    const child = run("git", args, { cwd: repo, env: { ...process.env, ...env }, maxBuffer: MAX });
    if (input !== undefined) { child.child.stdin.end(input); }
    const { stdout } = await child;
    return stdout;
  } catch (err) {
    const detail = (err.stderr || err.stdout || err.message || "").toString().trim();
    throw new Error(`git ${args.join(" ")}: ${detail}`);
  }
}

/** The top of the working tree that holds `path`, or null when it is not in a repository. */
export async function repoRoot(path) {
  try { return (await git(path, ["rev-parse", "--show-toplevel"])).trim(); } catch { return null; }
}

/** The commit HEAD names, or null before the first commit. */
export async function head(repo) {
  try { return (await git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"])).trim() || null; } catch { return null; }
}

export async function currentBranch(repo) {
  return (await git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])).trim();
}

/** The hash of the empty tree, the parent tree of a first commit. */
export async function emptyTree(repo) {
  return (await git(repo, ["hash-object", "-t", "tree", "/dev/null"])).trim();
}

/** The tree of a revision. */
export async function treeOf(repo, rev) {
  return (await git(repo, ["rev-parse", `${rev}^{tree}`])).trim();
}

/** Run `fn(env)` with a temporary index that starts as the tree given (or empty), then remove it. */
async function withTempIndex(repo, baseTree, fn) {
  const dir = await mkdtemp(join(tmpdir(), "chap-index-"));
  const env = { GIT_INDEX_FILE: join(dir, "index") };
  try {
    if (baseTree) await git(repo, ["read-tree", baseTree], { env });
    else await git(repo, ["read-tree", "--empty"], { env });
    return await fn(env);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

// Renames are not detected, so every file in a patch is added, deleted or
// modified under one path, and the desk can show and edit it as such.
const DIFF = ["-c", "core.quotepath=off", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false"];

/**
 * The working tree as a patch against `base` (default HEAD): tracked
 * changes and untracked files that are not ignored, binary files included.
 * Empty when there is nothing to propose.
 */
export async function workingTreePatch(repo, { base = "HEAD" } = {}) {
  const baseTree = (await head(repo)) ? await treeOf(repo, base) : null;
  return withTempIndex(repo, baseTree, async (env) => {
    await git(repo, ["add", "-A", "--", "."], { env });
    const against = baseTree ?? await emptyTree(repo);
    return git(repo, [...DIFF, "diff", "--cached", "--binary", "--no-renames", "--no-color", "--no-ext-diff", against], { env });
  });
}

/** A file's content at a revision, or null when it is not there. */
export async function fileAt(repo, rev, path) {
  try { return await git(repo, ["show", `${rev}:${path}`]); } catch { return null; }
}

/** The staged change as a patch, from the index a commit is being made from. */
export async function stagedPatch(repo) {
  const parent = (await head(repo)) ? "HEAD" : await emptyTree(repo);
  return git(repo, [...DIFF, "diff", "--cached", "--binary", "--no-renames", "--no-color", "--no-ext-diff", parent]);
}

/** The tree the index holds right now. */
export async function stagedTree(repo) {
  return (await git(repo, ["write-tree"])).trim();
}

/** The tree that results from applying `patch` to `baseTree`. Throws when the patch does not apply. */
export async function treeAfterPatch(repo, baseTree, patch) {
  return withTempIndex(repo, baseTree, async (env) => {
    if (patch.trim()) await git(repo, ["apply", "--cached", "--binary", "--whitespace=nowarn", "-"], { env, input: patch });
    return (await git(repo, ["write-tree"], { env })).trim();
  });
}

/** What a patch touches: one row per file with the lines added and removed ("-" for binary). */
export async function patchStats(repo, patch) {
  if (!patch.trim()) return [];
  const out = await git(repo, ["apply", "--numstat", "--binary", "-"], { input: patch });
  return out.split("\n").filter(Boolean).map((line) => {
    const [added, removed, ...rest] = line.split("\t");
    return { path: rest.join("\t"), added: added === "-" ? null : Number(added), removed: removed === "-" ? null : Number(removed) };
  });
}

/** Apply a patch to the working tree, or take it back out with `reverse`. */
export async function applyToWorkingTree(repo, patch, { reverse = false } = {}) {
  if (!patch.trim()) return;
  await git(repo, ["apply", "--binary", "--whitespace=nowarn", ...(reverse ? ["-R"] : []), "-"], { input: patch });
}

/** Whether a patch applies to the working tree as it is. */
export async function patchApplies(repo, patch, { reverse = false } = {}) {
  if (!patch.trim()) return true;
  try { await git(repo, ["apply", "--check", "--binary", ...(reverse ? ["-R"] : []), "-"], { input: patch }); return true; } catch { return false; }
}

/** Stage everything and commit with the message given. Returns the new commit. */
export async function commitAll(repo, message, { sign = false, env = {} } = {}) {
  await git(repo, ["add", "-A", "--", "."], { env });
  await git(repo, ["commit", "--quiet", ...(sign ? ["-S"] : []), "-F", "-"], { input: message, env });
  return (await git(repo, ["rev-parse", "HEAD"])).trim();
}

/** The trailers of a commit message: [{ token, value }]. */
export async function parseTrailers(repo, message) {
  const out = await git(repo, ["interpret-trailers", "--parse", "--only-trailers"], { input: message });
  return out.split("\n").filter(Boolean).map((line) => {
    const i = line.indexOf(":");
    return { token: line.slice(0, i).trim(), value: line.slice(i + 1).trim() };
  });
}

/** Add trailers to a commit message file in place. */
export async function addTrailersToFile(repo, file, pairs) {
  const args = ["interpret-trailers", "--in-place", "--if-exists", "replace"];
  for (const [token, value] of pairs) args.push("--trailer", `${token}: ${value}`);
  await git(repo, [...args, file]);
}

const NOTES_REF = "refs/notes/chap";

/** Write a note beside a commit under refs/notes/chap, replacing any there. */
export async function writeNote(repo, sha, text) {
  await git(repo, ["notes", `--ref=${NOTES_REF}`, "add", "-f", "-F", "-", sha], { input: text });
}

/** The note beside a commit under refs/notes/chap, or null. */
export async function readNote(repo, sha) {
  try { return await git(repo, ["notes", `--ref=${NOTES_REF}`, "show", sha]); } catch { return null; }
}

/** The commits of a range, oldest first. A single revision names one commit. */
export async function revList(repo, range) {
  const out = await git(repo, ["rev-list", "--reverse", ...(range.includes("..") ? [range] : ["-1", range])]);
  return out.split("\n").filter(Boolean);
}

/** A commit's hash, parents, tree, author and message. */
export async function commitInfo(repo, sha) {
  const out = await git(repo, ["show", "-s", "--format=%H%n%P%n%T%n%an <%ae>%n%ci%n%B", sha]);
  const [hash, parents, tree, author, date, ...rest] = out.split("\n");
  return { hash, parents: parents.split(" ").filter(Boolean), tree, author, date, message: rest.join("\n").trim() };
}

/** Whether git itself verifies the commit's signature (GPG or SSH, per the repository's configuration). */
export async function gitSignatureVerifies(repo, sha) {
  try { await git(repo, ["verify-commit", sha]); return true; } catch { return false; }
}

/** The commits on `rev` whose message carries the trailer given, newest first. */
export async function commitsWithTrailer(repo, token, value, rev = "HEAD") {
  try {
    const out = await git(repo, ["log", "--format=%H", `--grep=^${token}: ${value}$`, rev]);
    return out.split("\n").filter(Boolean);
  } catch { return []; }
}
