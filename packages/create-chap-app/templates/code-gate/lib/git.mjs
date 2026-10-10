// Git, as the gate uses it: the patch of a change, the tree a patch
// produces, commits with trailers and signatures, and notes beside commits.
//
// Every call runs the git on PATH in the repository given. The index is
// never touched except by `commitAll`: a patch is taken through a temporary
// index, and so is the tree a patch produces. Inside a hook, git sets
// GIT_INDEX_FILE to the index the commit is being made from, and the
// functions that read the staged change honour it.
//
// A patch the gate writes or checks is always git's own diff of two trees
// with every setting that shapes the text fixed, whatever the repository or
// the user has configured: full object ids, Myers' algorithm, three lines of
// context, no renames, no textconv or external diff, unquoted paths, files
// in path order, submodules always shown. The same change gives the same
// text on any machine, so an approved patch can be checked against the
// change it claims to make: `canonicalCheck` applies it to a tree, diffs the
// result the same way, and compares. A patch written by hand to show one
// thing and apply another does not survive that.

import { execFile } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);
const MAX = 64 * 1024 * 1024;

/** The environment for git: the caller's, less what would change a diff's text. */
function gitEnv(extra) {
  const env = { ...process.env, ...extra };
  for (const name of ["GIT_DIFF_OPTS", "GIT_EXTERNAL_DIFF"]) delete env[name];
  return env;
}

/** Run git in `repo`. Returns stdout, or a Buffer with `buffer`. A failure throws with git's message. */
export async function git(repo, args, { env = {}, input, buffer = false } = {}) {
  try {
    const child = run("git", args, { cwd: repo, env: gitEnv(env), maxBuffer: MAX, ...(buffer ? { encoding: "buffer" } : {}) });
    if (input !== undefined) child.child.stdin.end(input);
    const { stdout } = await child;
    return stdout;
  } catch (err) {
    const detail = (err.stderr || err.stdout || err.message || "").toString().trim();
    throw Object.assign(new Error(`git ${args.join(" ")}: ${detail}`), { stderr: detail });
  }
}

/**
 * Run git on a repository's objects with no attributes in play: from an
 * empty directory as the working tree, with an empty index unless one is
 * given, and no attributes file. A .gitattributes in the repository, the
 * change's own included, then decides nothing about how a diff is written
 * or which files are binary: git looks at the content alone.
 */
async function neutral(repo, args, { env = {}, input, buffer = false } = {}) {
  const gitDir = (await git(repo, ["rev-parse", "--absolute-git-dir"])).trim();
  const dir = await mkdtemp(join(tmpdir(), "chap-neutral-"));
  try {
    return await git(dir, ["-c", "core.attributesFile=/dev/null", `--git-dir=${gitDir}`, `--work-tree=${dir}`, ...args], {
      env: { GIT_INDEX_FILE: join(dir, "index"), GIT_ATTR_NOSYSTEM: "1", ...env }, input, buffer,
    });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/** Whether content is binary as git decides it with no attributes: a NUL byte in its first 8000 bytes. */
export function isBinary(buf) {
  return buf !== null && buf.subarray(0, 8000).includes(0);
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

/** A path inside the repository's git directory, absolute, as `git rev-parse --git-path` names it. */
export async function gitPath(repo, name) {
  return resolve(repo, (await git(repo, ["rev-parse", "--git-path", name])).trim());
}

/** Whether a merge is in progress: a commit now would be a merge commit. */
export async function mergeInProgress(repo) {
  try { await git(repo, ["rev-parse", "--verify", "--quiet", "MERGE_HEAD"]); return true; } catch { return false; }
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

const DIFF_CONFIG = [
  "-c", "core.quotepath=off", "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
  "-c", "diff.suppressBlankEmpty=false", "-c", "color.ui=never",
];
const DIFF_FLAGS = [
  "--binary", "--full-index", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv",
  "--diff-algorithm=myers", "--unified=3", "--inter-hunk-context=0", "--no-indent-heuristic",
  "--ignore-submodules=none", "--no-relative", "-O/dev/null",
];

/** The canonical patch from one tree to another. */
export async function treeDiff(repo, fromTree, toTree) {
  return neutral(repo, [...DIFF_CONFIG, "diff", ...DIFF_FLAGS, fromTree, toTree]);
}

/** What a change touches, from tree to tree: one row per file, lines added and removed (null for binary). */
export async function numstat(repo, fromTree, toTree) {
  const out = await neutral(repo, [...DIFF_CONFIG, "diff", "--numstat", "-z", "--no-renames", "--no-ext-diff", "--no-textconv", "--ignore-submodules=none", "--no-relative", "-O/dev/null", fromTree, toTree]);
  const rows = [];
  for (const record of out.split("\0")) {
    if (!record) continue;
    const [added, removed, ...rest] = record.split("\t");
    rows.push({ path: rest.join("\t"), added: added === "-" ? null : Number(added), removed: removed === "-" ? null : Number(removed) });
  }
  return rows;
}

/** A patch with the text after each hunk header's second @@ dropped: the function context, which git apply ignores. */
export function normalisePatch(patch) {
  return patch.replace(/^(@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@).*$/gm, "$1");
}

/**
 * The working tree as a change against HEAD: tracked changes and untracked
 * files that are not ignored, binary files included, taken through a
 * temporary index. Returns { baseTree, tree, patch }; the patch is empty
 * when there is nothing to propose.
 */
export async function workingTreeChange(repo) {
  const baseTree = (await head(repo)) ? await treeOf(repo, "HEAD") : await emptyTree(repo);
  return withTempIndex(repo, (await head(repo)) ? baseTree : null, async (env) => {
    await git(repo, ["add", "-A", "--", "."], { env });
    const tree = (await git(repo, ["write-tree"], { env })).trim();
    return { baseTree, tree, patch: tree === baseTree ? "" : await treeDiff(repo, baseTree, tree) };
  });
}

/** The working tree's change against HEAD as a canonical patch. */
export async function workingTreePatch(repo) {
  return (await workingTreeChange(repo)).patch;
}

/** A blob's content by tree and path, or null when the path is not a file there. */
export async function blobAt(repo, tree, path) {
  try {
    const type = (await git(repo, ["cat-file", "-t", `${tree}:${path}`])).trim();
    if (type !== "blob") return null;
    return await git(repo, ["cat-file", "blob", `${tree}:${path}`]);
  } catch { return null; }
}

/** A file's content at a revision, or null when it is not there. */
export async function fileAt(repo, rev, path) {
  return blobAt(repo, rev, path);
}

/** The tree the index holds right now. */
export async function stagedTree(repo) {
  return (await git(repo, ["write-tree"])).trim();
}

/** The staged change as a canonical patch, from the index a commit is being made from. */
export async function stagedPatch(repo) {
  const parent = (await head(repo)) ? await treeOf(repo, "HEAD") : await emptyTree(repo);
  return treeDiff(repo, parent, await stagedTree(repo));
}

/** The tree that results from applying `patch` to `baseTree`. Throws when the patch does not apply. */
export async function treeAfterPatch(repo, baseTree, patch) {
  return withTempIndex(repo, baseTree, async (env) => {
    if (patch.trim()) await neutral(repo, ["apply", "--cached", "--binary", "--whitespace=nowarn", "-"], { env, input: patch });
    return (await git(repo, ["write-tree"], { env })).trim();
  });
}

/** The files that differ between two trees: [{ path, oldMode, newMode, oldId, newId, status }]. */
export async function rawDiff(repo, fromTree, toTree) {
  const out = await neutral(repo, ["diff", "--raw", "-z", "--no-renames", "--abbrev=40", "--ignore-submodules=none", "--no-relative", fromTree, toTree]);
  const parts = out.split("\0");
  const rows = [];
  for (let i = 0; i + 1 < parts.length; i += 2) {
    const meta = parts[i];
    if (!meta.startsWith(":")) break;
    const [oldMode, newMode, oldId, newId, status] = meta.slice(1).split(" ");
    rows.push({ path: parts[i + 1], oldMode, newMode, oldId, newId, status });
  }
  return rows;
}

/**
 * Whether what the desk shows of `patch` is the change git makes with it.
 * The patch is applied to `baseTree` by git; the desk's own parser reads
 * it; and every file git changed must be a section the desk shows, with the
 * same kind of change and mode, whose hunks, applied the way the desk
 * reads them to the file as it was, give the file as git left it. A patch
 * with anything the desk cannot show (text before the first file, names
 * that differ between its header lines, a binary section for a text file,
 * a submodule) fails. Returns { ok, tree, reason }.
 */
export async function faithfulCheck(repo, baseTree, patch) {
  const { parsePatch, applyFile } = await import("../desk/diff.js");
  let tree;
  try { tree = await treeAfterPatch(repo, baseTree, patch); } catch (e) {
    return { ok: false, tree: null, reason: `the patch does not apply: ${String(e.stderr ?? e.message).split("\n")[0]}` };
  }
  const fail = (reason) => ({ ok: false, tree, reason: `what a reviewer sees of the patch is not what git applies: ${reason}` });
  const files = parsePatch(patch);
  if (files.anomalies.length) return fail(files.anomalies[0]);
  const sections = new Map(files.map((f) => [f.path, f]));
  const changes = await rawDiff(repo, baseTree, tree);
  if (changes.length !== sections.size) return fail(`it shows ${sections.size} file${sections.size === 1 ? "" : "s"} and changes ${changes.length}`);
  const bytes = async (id) => (/^0+$/.test(id) ? null : git(repo, ["cat-file", "blob", id], { buffer: true }));
  const blob = async (id) => (/^0+$/.test(id) ? null : git(repo, ["cat-file", "blob", id]));
  for (const c of changes) {
    const f = sections.get(c.path);
    if (!f) return fail(`it changes ${c.path}, which it does not show`);
    if (c.oldMode === "160000" || c.newMode === "160000") return fail(`it changes the submodule ${c.path}, which the gate does not take`);
    const kind = c.status === "A" ? "added" : c.status === "D" ? "deleted" : "modified";
    if (f.status !== kind) return fail(`${c.path} is ${kind} by git and shown as ${f.status}`);
    if (kind === "added" && f.mode !== c.newMode) return fail(`${c.path} is added with mode ${c.newMode} and shown with ${f.mode}`);
    if (kind === "deleted" && f.mode !== c.oldMode) return fail(`${c.path} is deleted with mode ${c.oldMode} and shown with ${f.mode}`);
    if (kind === "modified" && c.oldMode !== c.newMode && (f.oldMode !== c.oldMode || f.newMode !== c.newMode)) return fail(`${c.path} changes mode from ${c.oldMode} to ${c.newMode}, which it does not show`);
    if (kind === "modified" && c.oldMode === c.newMode && (f.oldMode || f.newMode)) return fail(`${c.path} is shown with a mode change git does not make`);
    const binary = isBinary(await bytes(c.oldId)) || isBinary(await bytes(c.newId));
    if (f.binary) {
      if (!binary) return fail(`${c.path} is shown as binary, and its content is text`);
      continue;
    }
    if (binary) return fail(`${c.path} is a binary file shown as text`);
    const before = kind === "added" ? null : await blob(c.oldId);
    const after = kind === "deleted" ? "" : await blob(c.newId);
    const shown = applyFile(before, { ...f, status: kind === "deleted" ? "modified" : f.status });
    if (shown !== after) return fail(`${c.path} reads differently in the patch from the file git writes`);
  }
  return { ok: true, tree, reason: null };
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

/**
 * Stage everything and commit with the message given. Returns the new
 * commit. With `signingKey`, the path of an OpenSSH private key, the commit
 * is signed with it (git's SSH signing), whatever the repository's own
 * signing settings are.
 */
export async function commitAll(repo, message, { signingKey = null, env = {} } = {}) {
  await git(repo, ["add", "-A", "--", "."], { env });
  const signing = signingKey ? ["-c", "gpg.format=ssh", "-c", `user.signingkey=${signingKey}`] : [];
  await git(repo, [...signing, "commit", "--quiet", ...(signingKey ? ["-S"] : []), "-F", "-"], { input: message, env });
  return (await git(repo, ["rev-parse", "HEAD"])).trim();
}

/** The commit's signature block, or null when it is unsigned. */
export async function commitSignature(repo, sha) {
  const raw = await git(repo, ["cat-file", "commit", sha]);
  const lines = raw.split("\n");
  const at = lines.findIndex((l) => l.startsWith("gpgsig "));
  if (at < 0) return null;
  const sig = [lines[at].slice("gpgsig ".length)];
  for (let i = at + 1; i < lines.length && lines[i].startsWith(" "); i++) sig.push(lines[i].slice(1));
  const text = sig.join("\n");
  return { text, ssh: text.startsWith("-----BEGIN SSH SIGNATURE-----") };
}

/**
 * Whether git verifies the commit's SSH signature against the allowed
 * signers file given. Returns { ok, detail } with git's own words.
 */
export async function verifySshSignature(repo, sha, allowedSignersFile) {
  try {
    const { stderr } = await run("git", ["-c", `gpg.ssh.allowedSignersFile=${allowedSignersFile}`, "verify-commit", sha], { cwd: repo, env: gitEnv({}), maxBuffer: MAX });
    return { ok: true, detail: (stderr || "").trim().split("\n").find((l) => l.includes("Good")) ?? "good signature" };
  } catch (e) {
    return { ok: false, detail: ((e.stderr || e.message || "").toString().trim().split("\n")[0]) || "the signature does not verify" };
  }
}

/** The trailers of a commit message: [{ token, value }]. */
export async function parseTrailers(repo, message) {
  const out = await git(repo, ["interpret-trailers", "--parse", "--only-trailers"], { input: message });
  return out.split("\n").filter(Boolean).map((line) => {
    const i = line.indexOf(":");
    return { token: line.slice(0, i).trim(), value: line.slice(i + 1).trim() };
  });
}

/**
 * Set the CHAP trailers of a commit message file: every existing CHAP-*
 * line is removed, then each pair is added, so a token given twice (two
 * reviewers) appears twice.
 */
export async function setTrailers(repo, file, pairs) {
  const { readFile, writeFile } = await import("node:fs/promises");
  const text = await readFile(file, "utf8");
  await writeFile(file, text.split("\n").filter((l) => !/^CHAP-[A-Za-z-]+:/.test(l)).join("\n"));
  const args = ["interpret-trailers", "--in-place", "--if-exists", "add", "--if-missing", "add"];
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

/** The commits on any ref whose message carries the trailer given, newest first. */
export async function commitsWithTrailer(repo, token, value, revs = ["--all"]) {
  try {
    const escaped = value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const out = await git(repo, ["log", "--format=%H", "-E", `--grep=^${token}: ${escaped}$`, ...revs]);
    return out.split("\n").filter(Boolean);
  } catch { return []; }
}

/**
 * The tree a clean merge of two commits gives, or null when they conflict
 * or this git cannot say (git merge-tree --write-tree arrived in 2.38).
 */
export async function cleanMergeTree(repo, a, b) {
  try {
    const out = await git(repo, ["merge-tree", "--write-tree", "--no-messages", a, b]);
    return out.split("\n")[0].trim() || null;
  } catch { return null; }
}
