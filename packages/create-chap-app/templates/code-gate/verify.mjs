// Verify that commits were approved through the gate: `node verify.mjs [range] [options]`.
//
//   range                  commits to check, e.g. main..HEAD (default: HEAD alone)
//   --repo <path>          the repository (default: the current directory)
//   --coordinator <url>    also check each task against a running gate (POST /chap address)
//   --allow-git-signed     let a commit with no CHAP approval through when git verifies
//                          its own signature (git verify-commit), for people's commits
//   --allow-unsigned       accept a decision envelope with no signature (signatures off)
//   --json                 print the results as JSON
//
// For each commit with CHAP trailers the note under refs/notes/chap is
// read and checked: the approved artefact's digest matches the trailer,
// the decision envelope names the task and the reviewer and signs the
// digest of what was proposed, an override's operations lead from the
// proposed artefact to the approved one, the reviewer's signature verifies
// against the key on record, and the approved patch applied to the commit's
// parent gives the commit's tree. None of that needs the coordinator. With
// --coordinator the task is read there too and must be completed with that
// decision. A commit with no trailers fails, unless --allow-git-signed
// applies; a merge commit is reported and passed. Exit code 1 on any failure.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { applyJsonPatch, contentHash, envelopeVerifies, lastDecision, loadGate, task as readTask } from "./lib/gate.mjs";
import { commitInfo, emptyTree, gitSignatureVerifies, parseTrailers, readNote, repoRoot, revList, treeAfterPatch } from "./lib/git.mjs";
import { deepEqual } from "./desk/chap-client.mjs";

const here = dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const out = { range: "HEAD", repo: process.cwd(), coordinator: process.env.CHAP_URL ?? null, allowGitSigned: false, allowUnsigned: false, json: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") out.repo = argv[++i];
    else if (a === "--coordinator") out.coordinator = argv[++i];
    else if (a === "--allow-git-signed") out.allowGitSigned = true;
    else if (a === "--allow-unsigned") out.allowUnsigned = true;
    else if (a === "--json") out.json = true;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}`);
    else out.range = a;
  }
  return out;
}

/** Check one commit. Returns { sha, subject, status: "ok" | "FAIL" | "merge", detail, task_id?, reviewer? }. */
export async function verifyCommit(repo, sha, { coordinator = null, allowGitSigned = false, allowUnsigned = false } = {}) {
  const info = await commitInfo(repo, sha);
  const subject = info.message.split("\n")[0];
  const row = (status, detail, extra = {}) => ({ sha, subject, status, detail, ...extra });
  if (info.parents.length > 1) return row("merge", "a merge commit; its parents are checked, it is not a change of its own");
  const trailers = Object.fromEntries((await parseTrailers(repo, info.message)).map((t) => [t.token, t.value]));
  const taskId = trailers["CHAP-Task"];
  if (!taskId) {
    if (allowGitSigned && (await gitSignatureVerifies(repo, sha))) return row("ok", "no CHAP approval; git verifies the commit's own signature");
    return row("FAIL", "no CHAP approval: the message carries no CHAP-Task trailer");
  }
  const text = await readNote(repo, sha);
  if (!text) return row("FAIL", `no evidence note for ${taskId}; fetch it with: git fetch origin refs/notes/chap:refs/notes/chap`, { task_id: taskId });
  let note;
  try { note = JSON.parse(text); } catch { return row("FAIL", "the evidence note is not JSON", { task_id: taskId }); }
  const problems = [];
  if (note.task_id !== taskId) problems.push(`the note is for ${note.task_id}, the trailer names ${taskId}`);
  if (trailers["CHAP-Workspace"] && note.workspace !== trailers["CHAP-Workspace"]) problems.push("the note's workspace differs from the trailer");
  const approved = note.approved_artefact;
  const proposed = note.proposed_artefact ?? approved;
  if (!approved || typeof approved.patch !== "string") problems.push("the note holds no approved patch");
  const approvedDigest = approved ? await contentHash(approved) : null;
  if (approvedDigest && trailers["CHAP-Artefact"] && approvedDigest !== trailers["CHAP-Artefact"]) problems.push("the approved artefact's digest differs from the CHAP-Artefact trailer");
  const env = note.decision_envelope;
  const params = env?.params ?? {};
  const method = env?.method;
  if (!["decide.approve", "decide.override"].includes(method)) problems.push(`the decision is ${method ?? "missing"}, not an approval`);
  if (params.task_id !== taskId) problems.push("the decision envelope names another task");
  const reviewer = params.from;
  if (trailers["CHAP-Reviewer"] && reviewer !== trailers["CHAP-Reviewer"]) problems.push("the decision envelope's sender differs from the CHAP-Reviewer trailer");
  if (proposed) {
    const proposedDigest = await contentHash(proposed);
    if (params.approved_artefact_digest !== proposedDigest) problems.push("the decision does not sign the digest of the proposed artefact");
    if (method === "decide.override") {
      try {
        if (!deepEqual(applyJsonPatch(proposed, params.diff ?? []), approved)) problems.push("the reviewer's operations applied to the proposed artefact do not give the approved one");
      } catch (e) { problems.push(`the reviewer's operations do not apply: ${e.message}`); }
    } else if (!deepEqual(proposed, approved)) {
      problems.push("an approval, yet the approved artefact differs from the proposed one");
    }
  }
  if (env?.sig) {
    const v = envelopeVerifies(env, note.reviewer_keys ?? []);
    if (!v.ok) problems.push(`the reviewer's signature: ${v.reason}`);
  } else if (!allowUnsigned) {
    problems.push("the decision envelope is unsigned (pass --allow-unsigned where signatures are off)");
  }
  if (approved?.patch !== undefined) {
    const parentTree = info.parents.length ? `${info.parents[0]}^{tree}` : await emptyTree(repo);
    try {
      const tree = await treeAfterPatch(repo, parentTree, approved.patch);
      if (tree !== info.tree) problems.push("the approved patch applied to the parent does not give this commit's tree");
    } catch (e) { problems.push(`the approved patch does not apply to the parent: ${e.message.split("\n")[0]}`); }
  }
  let online = null;
  if (coordinator) {
    const gate = { base: coordinator.replace(/\/chap\/?$/, "") };
    const view = await readTask(gate, taskId);
    if (!view) problems.push(`the coordinator at ${coordinator} does not know ${taskId}`);
    else {
      const d = lastDecision(view);
      if (view.state !== "completed") problems.push(`the coordinator holds ${taskId} as ${view.state}`);
      if (d?.kind !== (method === "decide.override" ? "override" : "approve") || d?.reviewer !== reviewer) problems.push("the coordinator's last decision differs from the note");
      if ((await contentHash(view.output)) !== approvedDigest) problems.push("the coordinator's approved artefact differs from the note");
      online = view.state;
    }
  }
  if (problems.length) return row("FAIL", problems.join("; "), { task_id: taskId, reviewer });
  const how = method === "decide.override" ? "approved with an edit" : "approved";
  return row("ok", `${how} by ${reviewer} as ${taskId}${env?.sig ? ", signed" : ""}${online ? `, ${online} at the coordinator` : ""}`, { task_id: taskId, reviewer });
}

export async function verifyRange(repoPath, range, options = {}) {
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${repoPath} is not inside a git repository`);
  const results = [];
  for (const sha of await revList(repo, range)) results.push(await verifyCommit(repo, sha, options));
  return results;
}

export function render(results) {
  const lines = results.map((r) => `${r.status.padEnd(5)} ${r.sha.slice(0, 12)}  ${r.subject.slice(0, 60).padEnd(60)}  ${r.detail}`);
  const failed = results.filter((r) => r.status === "FAIL").length;
  lines.push(failed ? `${failed} of ${results.length} commit${results.length === 1 ? "" : "s"} failed` : `${results.length} commit${results.length === 1 ? "" : "s"} verified`);
  return lines.join("\n");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  (async () => {
    const args = parseArgs(process.argv.slice(2));
    const coordinator = args.coordinator ?? null;
    const results = await verifyRange(args.repo, args.range, { coordinator, allowGitSigned: args.allowGitSigned, allowUnsigned: args.allowUnsigned });
    console.log(args.json ? JSON.stringify(results, null, 2) : render(results));
    process.exit(results.some((r) => r.status === "FAIL") ? 1 : 0);
  })().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}

