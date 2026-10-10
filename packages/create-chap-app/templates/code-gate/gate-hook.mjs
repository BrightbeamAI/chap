// The git hooks, in one file: `node gate-hook.mjs <pre-commit|commit-msg|post-commit>`.
//
// pre-commit   refuses the commit unless the staged change is exactly an
//              approved change: one approved against the commit's parent,
//              whose patch is git's own diff of the change it makes and
//              gives the tree being committed, not committed before, and
//              whose approvals hold under the trust policy: the
//              repository's chap-trust.json at HEAD when it has one, or
//              the reviewers chap.config.json names with the keys the
//              workspace records. The note is written to the repository's
//              chap/approval.json for the two hooks after it.
// commit-msg   sets the CHAP trailers for that approval in the message.
// post-commit  writes the evidence beside the commit as a note under
//              refs/notes/chap, when the commit made is the one approved.
//
// CHAP_GATE=off lets a commit through with no approval and no trailers; a
// verifier fails such a commit, so the bypass is visible where it matters.
// CHAP_URL names the gate when it is not at the address in chap.config.json.

import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { approvedChanges, buildNote, checkApproval, checkChange, evidence, loadGate, onlinePolicy, reviewRule, served, strictestRule, trailersFor, trustAtRef } from "./lib/gate.mjs";
import { commitInfo, commitsWithTrailer, emptyTree, gitPath, head, mergeInProgress, setTrailers, stagedTree, treeOf, writeNote } from "./lib/git.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const say = (line) => process.stderr.write(`chap: ${line}\n`);

const approvalFile = (repo) => gitPath(repo, "chap/approval.json");

async function readApproval(repo) {
  try { return JSON.parse(await readFile(await approvalFile(repo), "utf8")); } catch { return null; }
}

/** The approved changes whose patch, applied to the parent, gives the staged tree. */
export async function matchApprovals(gate, repo, { parent, parentTree, tree }) {
  const out = [];
  for (const t of await approvedChanges(gate, { base: parent })) {
    const problems = await checkChange(repo, { parent, parentTree, tree, approved: t.output });
    if (!problems.length) out.push(t);
  }
  return out;
}

async function preCommit(repo, gate) {
  await rm(await approvalFile(repo), { force: true });
  if (await mergeInProgress(repo)) {
    say("a merge commit cannot carry an approval: what it brings in was never proposed as one change.");
    say("rebase the branch onto its target, or commit with CHAP_GATE=off and let the verifier report it.");
    return 1;
  }
  const cfg = await served(gate);
  const parent = await head(repo);
  const parentTree = parent ? await treeOf(repo, parent) : await emptyTree(repo);
  const tree = await stagedTree(repo);
  if (tree === parentTree) {
    say("the commit changes nothing, so there is nothing an approval could cover. CHAP_GATE=off commits it anyway.");
    return 1;
  }
  const matches = await matchApprovals(gate, repo, { parent, parentTree, tree });
  if (!matches.length) {
    say("no approved change matches what is staged on this commit's parent.");
    say(`propose it from the repository with: node ${join(here, "propose.mjs")} "what the change does"`);
    say(`then decide at ${gate.base}/ and commit again. CHAP_GATE=off commits without an approval.`);
    return 1;
  }
  // Of the approvals that match the change, the first whose approval holds
  // under the policy; each one that does not says why.
  const pinned = parent ? await trustAtRef(repo, parent) : null;
  let chosen = null;
  const reasons = [];
  for (const match of matches) {
    const already = await commitsWithTrailer(repo, "CHAP-Task", match.task_id);
    if (already.length) { reasons.push([match.task_id, [`was committed already, as ${already[0].slice(0, 12)}; an approval covers one commit. Propose the change again.`]]); continue; }
    const ev = await evidence(gate, match.task_id);
    const note = buildNote(ev, gate.url);
    const policy = pinned ? { ...pinned } : onlinePolicy(gate, ev);
    policy.rule = strictestRule([policy.rule, reviewRule(gate).rule], Object.keys(policy.reviewers));
    const problems = await checkApproval(note, policy, { allowUnsigned: !cfg.require_signatures });
    if (problems.length) { reasons.push([match.task_id, problems, policy.source]); continue; }
    chosen = { note, policy };
    break;
  }
  if (!chosen) {
    for (const [id, problems, source] of reasons) {
      say(`${id} ${problems.length === 1 && problems[0].startsWith("was committed") ? problems[0] : `is approved at the gate, and its approval does not hold under ${source}:`}`);
      if (!(problems.length === 1 && problems[0].startsWith("was committed"))) for (const problem of problems) say(`  ${problem}`);
    }
    return 1;
  }
  const { note, policy } = chosen;
  const file = await approvalFile(repo);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify({ note, tree, parent }));
  const approvers = [...new Set(note.decisions.filter((d) => d.method !== "decide.reject").map((d) => d.reviewer))];
  say(`approved as ${note.task_id} by ${approvers.join(" and ")} (${note.decision.method === "decide.override" ? "with an edit" : policy.rule}), checked against ${policy.source}`);
  return 0;
}

async function commitMsg(repo, messageFile) {
  const approval = await readApproval(repo);
  if (!approval) { say("no approval recorded by pre-commit; the message gets no trailers"); return 0; }
  if (approval.tree !== (await stagedTree(repo))) {
    say("the change staged now is not the one pre-commit approved; commit again");
    return 1;
  }
  await setTrailers(repo, messageFile, await trailersFor(approval.note));
  return 0;
}

async function postCommit(repo) {
  const approval = await readApproval(repo);
  if (!approval) return 0;
  await rm(await approvalFile(repo), { force: true });
  const sha = await head(repo);
  const info = await commitInfo(repo, sha);
  if (info.tree !== approval.tree || (info.parents[0] ?? null) !== (approval.parent ?? null)) {
    say(`${sha.slice(0, 12)} is not the change pre-commit approved (an amend, or a commit made without the hooks); no note written`);
    return 0;
  }
  await writeNote(repo, sha, JSON.stringify(approval.note, null, 2) + "\n");
  say(`${sha.slice(0, 12)} carries ${approval.note.task_id}; the evidence is in refs/notes/chap. Push it with: node ${join(here, "push-notes.mjs")}`);
  return 0;
}

export async function hook(stage, argv, repo = process.cwd()) {
  if (process.env.CHAP_GATE === "off") {
    if (stage === "pre-commit") { await rm(await approvalFile(repo), { force: true }); say("CHAP_GATE=off: this commit carries no approval"); }
    return 0;
  }
  const gate = await loadGate(here);
  if (stage === "pre-commit") return preCommit(repo, gate);
  if (stage === "commit-msg") return commitMsg(repo, argv[0]);
  if (stage === "post-commit") return postCommit(repo);
  say(`unknown hook ${stage}`);
  return 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  hook(process.argv[2], process.argv.slice(3)).then((code) => process.exit(code)).catch((e) => {
    say(e instanceof Error ? e.message : String(e));
    process.exit(1);
  });
}
