// The git hooks, in one file: `node gate-hook.mjs <pre-commit|commit-msg|post-commit|pre-push>`.
//
// pre-commit   refuses the commit unless the staged change is exactly an
//              approved change: one approved against the commit's parent,
//              whose patch is git's own diff of the change it makes and
//              gives the tree being committed, unused by any earlier commit, and
//              whose approvals hold under the trust policy: the
//              repository's chap-trust.json at HEAD when it has one, or
//              the reviewers chap.config.json names with the keys the
//              workspace records. The note is written to the repository's
//              chap/approval.json for the two hooks after it.
// commit-msg   sets the trailers for that approval in the message: who
//              reviewed it, by name and email, the model that wrote it, and
//              the CHAP record.
// post-commit  writes the evidence beside the commit as a note under
//              refs/notes/chap, when the commit made is the one approved.
// pre-push     refuses the push unless every commit it sends that the remote
//              does not have is approved through the gate: a change approved
//              before it was committed, or a commit of a branch approved and
//              sealed with propose-branch.mjs, each checked as verify.mjs
//              checks it.
//
// With "review_at": "push" in chap.config.json, agents commit on their own
// and their branch is reviewed before it is pushed: pre-commit then lets an
// unapproved commit through, says so, and gives an approved one its
// trailers as before, and pre-push is the gate.
//
// CHAP_GATE=off lets a commit through with no approval and no trailers; a
// verifier fails such a commit, so the bypass is visible where it matters.
// CHAP_URL names the gate when it is not at the address in chap.config.json.

import { readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { approvedChanges, buildNote, checkApproval, checkChange, evidence, loadGate, onlinePolicy, reviewRule, served, strictestRule, trailersFor, trustAtRef } from "./lib/gate.mjs";
import { commitInfo, commitsWithTrailer, emptyTree, git, head, mergeInProgress, objectExists, setTrailers, stagedTree, treeOf, writeNote } from "./lib/git.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const say = (line) => process.stderr.write(`chap: ${line}\n`);
/** A path as a shell command can take it. */
const quoted = (path) => (/[\s"'$`\\]/.test(path) ? JSON.stringify(path) : path);
const command = (script) => `node ${quoted(join(here, script))}`;

/** Where pre-commit leaves the approval for the hooks after it: in this worktree's own git directory. */
const approvalFile = async (repo) => join((await git(repo, ["rev-parse", "--absolute-git-dir"])).trim(), "chap-approval.json");

/** Where approval is required: "commit" (each change before it is committed) or "push" (a branch before it is pushed). */
export function reviewAt(gate) {
  const at = gate.config.review_at ?? "commit";
  if (at !== "commit" && at !== "push") throw new Error(`review_at in chap.config.json is ${JSON.stringify(at)}; it is "commit" or "push"`);
  return at;
}

async function readApproval(repo) {
  try { return JSON.parse(await readFile(await approvalFile(repo), "utf8")); } catch { return null; }
}

/**
 * The approved changes whose patch, applied to the parent, gives the staged
 * tree. Each approved change on the same parent that does not match goes
 * into `near` with what kept it out.
 */
export async function matchApprovals(gate, repo, { parent, parentTree, tree }, near = []) {
  const out = [];
  for (const t of await approvedChanges(gate, { base: parent })) {
    const problems = await checkChange(repo, { parent, parentTree, tree, approved: t.output });
    if (!problems.length) out.push(t);
    else near.push([t.task_id, problems]);
  }
  return out;
}

async function preCommit(repo, gate) {
  await rm(await approvalFile(repo), { force: true });
  const atPush = reviewAt(gate) === "push";
  // Under review_at push an unapproved commit is made, and reviewed with its
  // branch before it is pushed.
  const letThrough = (why) => {
    say(`${why}; under review_at push it is committed, and reviewed with its branch before it is pushed: ${command("propose-branch.mjs")} <base>..<branch> --wait`);
    return 0;
  };
  if (atPush) {
    // An agent that commits on its own commits whether or not the gate is up.
    try { return await preCommitChecked(repo, gate, atPush, letThrough); } catch (e) {
      if (e?.unreachable) return letThrough("the gate is not answering, so nothing is checked now");
      throw e;
    }
  }
  return preCommitChecked(repo, gate, atPush, letThrough);
}

async function preCommitChecked(repo, gate, atPush, letThrough) {
  if (atPush && (await mergeInProgress(repo))) return letThrough("a merge commit carries no approval of its own");
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
    if (atPush) return letThrough("the commit changes nothing");
    say("the commit changes nothing, so there is nothing an approval could cover. CHAP_GATE=off commits it anyway.");
    return 1;
  }
  const near = [];
  const matches = await matchApprovals(gate, repo, { parent, parentTree, tree }, near);
  if (!matches.length && atPush) return letThrough("no approved change matches what is staged");
  if (!matches.length) {
    say("no approved change matches what is staged on this commit's parent.");
    for (const [id, problems] of near.slice(0, 3)) say(`  ${id}, approved on this parent: ${problems[0]}`);
    say(`propose it from the repository with: ${command("propose.mjs")} "what the change does" --model "the model that wrote it"`);
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
  if (!chosen && atPush) return letThrough("no approval of what is staged holds");
  if (!chosen) {
    for (const [id, problems, source] of reasons) {
      say(`${id} ${problems.length === 1 && problems[0].startsWith("was committed") ? problems[0] : `is approved at the gate, and its approval does not hold under ${source}:`}`);
      if (!(problems.length === 1 && problems[0].startsWith("was committed"))) for (const problem of problems) say(`  ${problem}`);
    }
    return 1;
  }
  const { note, policy } = chosen;
  // The trailers are written now, under the policy the approval was checked
  // against: the reviewers' names and emails and their keys come from it.
  const trailers = await trailersFor(note, policy);
  await writeFile(await approvalFile(repo), JSON.stringify({ note, tree, parent, trailers }));
  const approvers = [...new Set(note.decisions.filter((d) => d.method !== "decide.reject").map((d) => d.reviewer))];
  say(`approved as ${note.task_id} by ${approvers.join(" and ")} (${note.decision.method === "decide.override" ? "with an edit" : policy.rule}), checked against ${policy.source}`);
  return 0;
}

async function commitMsg(repo, messageFile, gate) {
  const approval = await readApproval(repo);
  if (!approval) {
    if (reviewAt(gate) === "push") return 0;
    say("pre-commit recorded no approval for this commit; commit again");
    return 1;
  }
  if (approval.tree !== (await stagedTree(repo))) {
    say("the change staged now is not the one pre-commit approved; commit again");
    return 1;
  }
  await setTrailers(repo, messageFile, approval.trailers);
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
  say(`${sha.slice(0, 12)} carries ${approval.note.task_id}; the evidence is in refs/notes/chap. Push it with: ${command("push-notes.mjs")}`);
  return 0;
}

const ZERO = /^0+$/;

/**
 * The commits the remote has, as the remote itself lists its refs, kept to
 * those in this repository. Local remote-tracking refs are not consulted:
 * anyone with the repository can write them.
 */
async function remoteHas(repo, where) {
  let out;
  try { out = await git(repo, ["ls-remote", where]); } catch { return null; }
  const shas = new Map();
  for (const line of out.split("\n")) {
    const [sha, ref] = line.split("\t");
    if (!sha || !ref || ref.endsWith("^{}") || ref.startsWith("refs/notes/")) continue;
    if (await objectExists(repo, sha)) shas.set(ref, sha);
  }
  return shas;
}

/**
 * pre-push: every commit the push sends that the remote does not have must
 * hold as verify.mjs holds it, and an approved branch goes whole. The
 * policy is chap-trust.json as the remote's side of the ref has it, or as
 * the remote's default branch has it for a new ref, or the gate's records
 * when there is none. Clean merges pass; a merge carrying changes of its
 * own does not.
 */
async function prePush(repo, gate, [remote = "origin", url], input) {
  // Read here, so the commit hooks do not depend on the verifier.
  const { verifyCommit, seriesGaps } = await import("./verify.mjs");
  const has = await remoteHas(repo, url || remote);
  if (!has) say(`${remote} did not list its refs, so every commit not known to be there is checked`);
  const theirs = [...(has?.values() ?? [])];
  const seen = new Map();
  const failed = [];
  let checked = 0;
  for (const line of input.split("\n").map((l) => l.trim()).filter(Boolean)) {
    const [, localSha, remoteRef, remoteSha] = line.split(/\s+/);
    if (!localSha || ZERO.test(localSha) || remoteRef?.startsWith("refs/notes/")) continue;
    const known = !!remoteSha && !ZERO.test(remoteSha) && (await objectExists(repo, remoteSha));
    const exclude = [...new Set([...theirs, ...(known ? [remoteSha] : [])])];
    const out = await git(repo, ["rev-list", "--reverse", localSha, ...(exclude.length ? ["--not", ...exclude] : [])]);
    const shas = out.split("\n").filter(Boolean);
    if (!shas.length) continue;
    const policyAt = known ? remoteSha : (has?.get("HEAD") ?? has?.get("refs/heads/main") ?? has?.get("refs/heads/master") ?? null);
    const pinned = policyAt ? await trustAtRef(repo, policyAt) : null;
    for (const sha of shas) {
      const r = await verifyCommit(repo, sha, { policy: pinned, gate: pinned ? null : gate, seen, allowPeople: !!gate.config.allow_people, allowCleanMerges: true, allowUnsigned: !gate.config.require_signatures });
      checked++;
      if (r.status !== "ok") failed.push(r);
    }
  }
  const gaps = await seriesGaps(repo, seen, theirs);
  if (failed.length || gaps.length) {
    if (failed.length) {
      say(`${failed.length} of the ${checked} commit${checked === 1 ? "" : "s"} this push sends to ${remote} ${failed.length === 1 ? "has" : "have"} no approval that holds:`);
      for (const r of failed) say(`  ${r.sha.slice(0, 12)}  ${r.subject.slice(0, 50)}: ${r.detail}`);
    }
    for (const gap of gaps) say(`${gap.task_id} approved a branch of ${gap.of} commits, and this push leaves out commit${gap.missing.length === 1 ? "" : "s"} ${gap.missing.join(", ")} of it; an approved branch is pushed whole`);
    say(`review the branch first: ${command("propose-branch.mjs")} <base>..<branch> --wait`);
    say("CHAP_GATE=off pushes without this check, and the pull request check fails what is unapproved.");
    return 1;
  }
  if (checked) say(`${checked} commit${checked === 1 ? "" : "s"} checked for ${remote}: each approved through the gate`);
  return 0;
}

async function readStdin() {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString("utf8");
}

export async function hook(stage, argv, repo = process.cwd(), { input = null } = {}) {
  if (process.env.CHAP_GATE === "off") {
    if (stage === "pre-commit") { await rm(await approvalFile(repo), { force: true }); say("CHAP_GATE=off: this commit carries no approval"); }
    if (stage === "pre-push") say("CHAP_GATE=off: pushed without checking the commits");
    return 0;
  }
  const gate = await loadGate(here);
  if (stage === "pre-commit") return preCommit(repo, gate);
  if (stage === "commit-msg") return commitMsg(repo, argv[0], gate);
  if (stage === "post-commit") return postCommit(repo);
  if (stage === "pre-push") return prePush(repo, gate, argv, input ?? (await readStdin()));
  say(`unknown hook ${stage}`);
  return 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  hook(process.argv[2], process.argv.slice(3)).then((code) => process.exit(code)).catch((e) => {
    say(e instanceof Error ? e.message : String(e));
    process.exit(1);
  });
}
