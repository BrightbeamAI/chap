// Verify that commits were approved through the gate: `node verify.mjs [range] [options]`.
//
//   range                    commits to check, e.g. main..HEAD (default: HEAD alone)
//   --repo <path>            the repository (default: the current directory)
//   --coordinator <url>      also check each task against a running gate (POST /chap address)
//   --require-signed-commit  fail a commit the agent's key did not sign
//   --allow-git-signed       let a commit with no CHAP approval through when git verifies
//                            its own signature (git verify-commit), for people's commits
//   --allow-unsigned         accept decisions with no signature (signatures off)
//   --json                   print the results as JSON
//
// For each commit with CHAP trailers the note under refs/notes/chap is read
// and checked, with nothing but the repository: the approved artefact's
// digest matches the trailer; every decision of the final review round
// names the task, signs the digest of what was proposed, and verifies
// against the reviewer's key on record; an override's operations lead from
// the proposed artefact to the approved one; the review rule is met by
// that many distinct reviewers; and the approved patch applied to the
// commit's parent gives the commit's tree. A commit the gate made carries
// an SSH signature by the agent's key, which is checked against the key
// the note holds for the agent. With --coordinator the task is read there
// too and must be completed with the same artefact. A commit with no
// trailers fails, unless --allow-git-signed applies; a merge commit is
// reported and passed. Exit code 1 on any failure.

import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { checkNote, contentHash, task as readTask, outcomeOfView } from "./lib/gate.mjs";
import { commitInfo, commitSignature, emptyTree, gitSignatureVerifies, parseTrailers, readNote, repoRoot, revList, treeAfterPatch, verifySshSignature } from "./lib/git.mjs";
import { opensshPublicKey } from "./keys.mjs";

export function parseArgs(argv) {
  const out = { range: "HEAD", repo: process.cwd(), coordinator: process.env.CHAP_URL ?? null, requireSignedCommit: false, allowGitSigned: false, allowUnsigned: false, json: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") out.repo = argv[++i];
    else if (a === "--coordinator") out.coordinator = argv[++i];
    else if (a === "--require-signed-commit") out.requireSignedCommit = true;
    else if (a === "--allow-git-signed") out.allowGitSigned = true;
    else if (a === "--allow-unsigned") out.allowUnsigned = true;
    else if (a === "--json") out.json = true;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}`);
    else out.range = a;
  }
  return out;
}

/** Check the commit's SSH signature against the agent keys the note holds. */
async function commitSignedByAgent(repo, sha, note) {
  const sig = await commitSignature(repo, sha);
  if (!sig) return { signed: false };
  if (!sig.ssh) return { signed: true, ok: false, detail: "the commit carries a signature that is not an SSH signature" };
  const keys = (note.agent_keys ?? []).filter((k) => k?.kty === "OKP" && k?.crv === "Ed25519" && typeof k.x === "string");
  if (!keys.length) return { signed: true, ok: false, detail: "the note holds no key for the agent to check the commit's signature against" };
  const dir = await mkdtemp(join(tmpdir(), "chap-signers-"));
  try {
    const file = join(dir, "allowed_signers");
    await writeFile(file, keys.map((k) => `${note.agent} namespaces="git" ${opensshPublicKey(k).split(" ").slice(0, 2).join(" ")}`).join("\n") + "\n");
    const v = await verifySshSignature(repo, sha, file);
    return { signed: true, ok: v.ok, detail: v.detail };
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

/** Check one commit. Returns { sha, subject, status: "ok" | "FAIL" | "merge", detail, task_id?, reviewers? }. */
export async function verifyCommit(repo, sha, { coordinator = null, requireSignedCommit = false, allowGitSigned = false, allowUnsigned = false } = {}) {
  const info = await commitInfo(repo, sha);
  const subject = info.message.split("\n")[0];
  const row = (status, detail, extra = {}) => ({ sha, subject, status, detail, ...extra });
  if (info.parents.length > 1) return row("merge", "a merge commit; its parents are checked, it is not a change of its own");
  const trailerList = await parseTrailers(repo, info.message);
  const trailers = Object.fromEntries(trailerList.map((t) => [t.token, t.value]));
  const taskId = trailers["CHAP-Task"];
  if (!taskId) {
    if (allowGitSigned && (await gitSignatureVerifies(repo, sha))) return row("ok", "no CHAP approval; git verifies the commit's own signature");
    return row("FAIL", "no CHAP approval: the message carries no CHAP-Task trailer");
  }
  const text = await readNote(repo, sha);
  if (!text) return row("FAIL", `no evidence note for ${taskId}; fetch it with: git fetch origin refs/notes/chap:refs/notes/chap`, { task_id: taskId });
  let note;
  try { note = JSON.parse(text); } catch { return row("FAIL", "the evidence note is not JSON", { task_id: taskId }); }
  const problems = await checkNote(note, { taskId, allowUnsigned });
  if (trailers["CHAP-Workspace"] && note.workspace !== trailers["CHAP-Workspace"]) problems.push("the note's workspace differs from the trailer");
  const approvedDigest = note.approved_artefact ? await contentHash(note.approved_artefact) : null;
  if (approvedDigest && trailers["CHAP-Artefact"] && approvedDigest !== trailers["CHAP-Artefact"]) problems.push("the approved artefact's digest differs from the CHAP-Artefact trailer");
  const reviewers = [...new Set((note.decisions ?? []).filter((d) => d.method !== "decide.reject").map((d) => d.reviewer))];
  const trailerReviewers = trailerList.filter((t) => t.token === "CHAP-Reviewer").map((t) => t.value);
  if (trailerReviewers.length && reviewers.length && trailerReviewers.slice().sort().join(",") !== reviewers.slice().sort().join(",")) problems.push("the CHAP-Reviewer trailers differ from the decisions in the note");
  if (typeof note.approved_artefact?.patch === "string") {
    const parentTree = info.parents.length ? `${info.parents[0]}^{tree}` : await emptyTree(repo);
    try {
      const tree = await treeAfterPatch(repo, parentTree, note.approved_artefact.patch);
      if (tree !== info.tree) problems.push("the approved patch applied to the parent does not give this commit's tree");
    } catch (e) { problems.push(`the approved patch does not apply to the parent: ${e.message.split("\n")[0]}`); }
  }
  const signature = await commitSignedByAgent(repo, sha, note);
  if (signature.signed && !signature.ok) problems.push(`the commit's signature: ${signature.detail}`);
  if (!signature.signed && requireSignedCommit) problems.push("the commit is not signed by the agent's key");
  let online = null;
  if (coordinator) {
    const gate = { base: coordinator.replace(/\/chap\/?$/, "") };
    const view = await readTask(gate, taskId);
    if (!view) problems.push(`the coordinator at ${coordinator} does not know ${taskId}`);
    else {
      if (view.state !== "completed") problems.push(`the coordinator holds ${taskId} as ${view.state}`);
      if ((await contentHash(view.output)) !== approvedDigest) problems.push("the coordinator's approved artefact differs from the note");
      if (outcomeOfView(view) !== (note.decision?.method === "decide.override" ? "override" : "approve")) problems.push("the coordinator's decision differs from the note");
      online = view.state;
    }
  }
  if (problems.length) return row("FAIL", problems.join("; "), { task_id: taskId, reviewers });
  const how = note.decision?.method === "decide.override" ? "approved with an edit" : "approved";
  const by = reviewers.length > 1 ? `${reviewers.join(" and ")} (${note.rule})` : reviewers[0] ?? note.decision?.reviewer;
  const parts = [`${how} by ${by} as ${taskId}`, "decisions signed"];
  if (signature.signed) parts.push("commit signed by the agent's key");
  if (online) parts.push(`${online} at the coordinator`);
  return row("ok", parts.join(", "), { task_id: taskId, reviewers });
}

export async function verifyRange(repoPath, range, options = {}) {
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${repoPath} is not inside a git repository`);
  const results = [];
  for (const sha of await revList(repo, range)) results.push(await verifyCommit(repo, sha, options));
  return results;
}

export function render(results) {
  const lines = results.map((r) => `${r.status.padEnd(5)} ${r.sha.slice(0, 12)}  ${r.subject.slice(0, 50).padEnd(50)}  ${r.detail}`);
  const failed = results.filter((r) => r.status === "FAIL").length;
  lines.push(failed ? `${failed} of ${results.length} commit${results.length === 1 ? "" : "s"} failed` : `${results.length} commit${results.length === 1 ? "" : "s"} verified`);
  return lines.join("\n");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  (async () => {
    const args = parseArgs(process.argv.slice(2));
    const results = await verifyRange(args.repo, args.range, args);
    console.log(args.json ? JSON.stringify(results, null, 2) : render(results));
    process.exit(results.some((r) => r.status === "FAIL") ? 1 : 0);
  })().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}
