// Send the evidence notes with the commits: `node push-notes.mjs [--repo <path>] [--remote origin]`.
//
// Notes live under refs/notes/chap, apart from the branches, and several
// people pushing them write the same ref. This fetches the remote's notes,
// merges them with the local ones (each commit has its own note, so the two
// sets join), and pushes the result. Run it after pushing the commits.

import { fileURLToPath } from "node:url";
import { git, repoRoot } from "./lib/git.mjs";

export async function pushNotes(repoPath, { remote = "origin", log = console.log } = {}) {
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${repoPath} is not inside a git repository`);
  let fetched = true;
  try { await git(repo, ["fetch", remote, "+refs/notes/chap:refs/notes/chap-remote"]); } catch { fetched = false; }
  if (fetched) {
    let local = true;
    try { await git(repo, ["rev-parse", "--verify", "--quiet", "refs/notes/chap"]); } catch { local = false; }
    if (local) await git(repo, ["notes", "--ref=refs/notes/chap", "merge", "-s", "ours", "refs/notes/chap-remote"]);
    else await git(repo, ["update-ref", "refs/notes/chap", "refs/notes/chap-remote"]);
    await git(repo, ["update-ref", "-d", "refs/notes/chap-remote"]);
  }
  await git(repo, ["push", remote, "refs/notes/chap"]);
  log(`refs/notes/chap pushed to ${remote}${fetched ? ", merged with the notes already there" : ""}`);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const argv = process.argv.slice(2);
  const opt = (name, fallback) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : fallback; };
  pushNotes(opt("--repo", process.cwd()), { remote: opt("--remote", "origin") }).catch((e) => { console.error(e.message); process.exit(1); });
}
