// A branch under review: the commits an agent made on its own, proposed as
// one review and read commit by commit in the desk. Once approved, the
// branch is sealed: each commit is written again with the same tree, author
// and message, the gate's trailers after the message (who reviewed it, the
// model that wrote it, the commit the reviewers saw), signed with the
// agent's key, and the evidence beside it as a note. A pre-push hook and
// verify.mjs accept a sealed commit only when all of that holds.

import { basename } from "node:path";
import { api, approvalOf, contentHash, lastDecision, modelLabel, noteArtefacts, RANGE_KIND, sha256, submitForReview, trailersFor } from "./gate.mjs";
import { committerIdent, commitTree, emptyTree, faithfulCheck, git, GATE_TRAILER, numstat, parseTrailers, rawCommit, treeDiff, treeOf, writeNote } from "./git.mjs";

/** The most commits one branch review takes. */
export const MAX_RANGE_COMMITS = 100;

const short = (sha) => (sha ? String(sha).slice(0, 12) : "none");

/** The idempotency key of a branch review: the same commits on the same base are the same task. */
export function rangeKey(base, head) {
  return "range-" + sha256(`${base ?? "none"}\n${head}`).slice(0, 24);
}

/** A revision resolves to a commit, or the call fails with git's words. */
async function commitOf(repo, rev) {
  return (await git(repo, ["rev-parse", "--verify", "--quiet", `${rev}^{commit}`])).trim();
}


/**
 * A proposed commit's message as the reviewers see it and the sealed commit
 * carries it: every line in the gate's own form (Reviewed-by and CHAP-*)
 * dropped, wherever it stands, since only the gate writes those; trailing
 * blank lines dropped.
 */
export function proposedMessage(message) {
  return message.split("\n").filter((line) => !GATE_TRAILER.test(line)).join("\n").replace(/\s+$/, "");
}

/**
 * The commits of `base..head` as the reviewers will see them, oldest first.
 * They must be one line of plain commits, the first sitting where the branch
 * leaves `base`'s history; that commit is the branch's base. A merge, a
 * message in another encoding, or a change that cannot be shown as text is
 * refused, each with what to do. A message keeps no line in the gate's own
 * form: a commit sealed before and amended since is proposed again as it
 * now is.
 */
export async function describeRange(repo, { base, head = "HEAD", summary = null, drafted_by = null, model = null, branch = null } = {}) {
  const headSha = await commitOf(repo, head).catch(() => "");
  if (!headSha) throw new Error(`${head} is not a commit in ${repo}`);
  let fork = null;
  if (base) {
    const baseSha = await commitOf(repo, base).catch(() => "");
    if (!baseSha) throw new Error(`${base} is not a commit in ${repo}`);
    fork = await git(repo, ["merge-base", baseSha, headSha]).then((o) => o.trim(), () => "");
    if (!fork) throw new Error(`${head} shares no history with ${base}`);
  }
  const shas = (await git(repo, ["rev-list", "--reverse", fork ? `${fork}..${headSha}` : headSha])).split("\n").filter(Boolean);
  if (!shas.length) throw new Error(`${base}..${head} holds no commits to review`);
  if (shas.length > MAX_RANGE_COMMITS) throw new Error(`${base}..${head} holds ${shas.length} commits; a branch review takes ${MAX_RANGE_COMMITS} at most. Review it in parts.`);
  const raws = [];
  for (const sha of shas) {
    const raw = await rawCommit(repo, sha);
    if (raw.parents.length > 1) throw new Error(`${short(sha)} is a merge commit. A branch is reviewed as one line of commits: rebase it, then propose it again.`);
    raws.push(raw);
  }
  const commits = [];
  let parent = fork;
  let parentTree = fork ? await treeOf(repo, fork) : await emptyTree(repo);
  for (const raw of raws) {
    const sha = raw.sha;
    if ((raw.parents[0] ?? null) !== parent) throw new Error(`${short(sha)} does not sit on ${short(parent)}: the commits are not one line. Rebase the branch, then propose it again.`);
    if (raw.encoding && !/^utf-?8$/i.test(raw.encoding)) throw new Error(`${short(sha)} has a message in ${raw.encoding}; the gate reads UTF-8 only`);
    const message = proposedMessage(raw.message);
    const patch = await treeDiff(repo, parentTree, raw.tree);
    const check = await faithfulCheck(repo, parentTree, patch);
    if (!check.ok) throw new Error(`${short(sha)} cannot be shown to a reviewer as it stands: ${check.reason.replace(/^what a reviewer sees of the patch is not what git applies: /, "")}`);
    if (check.tree !== raw.tree) throw new Error(`${short(sha)} cannot be shown as text: a file in it is not UTF-8, so its patch would not give its tree back`);
    const files = await numstat(repo, parentTree, raw.tree);
    commits.push({ sha, parent, tree: raw.tree, author: raw.author, message, files, patch });
    parent = sha;
    parentTree = raw.tree;
  }
  const subject = (m) => m.split("\n")[0];
  return {
    summary: summary ?? (commits.length === 1 ? subject(commits[0].message) : `${commits.length} commits${branch ? ` on ${branch}` : ""}`),
    repo: basename(repo),
    branch: branch ?? null,
    base: fork,
    head: headSha,
    commits,
    ...(drafted_by ? { drafted_by } : {}),
    ...(model ? { model: modelLabel(model) } : {}),
  };
}

/**
 * This agent's reviews of the same repository and branch, newest first:
 * the one open (waiting, or sent back for a revision), and the ones
 * approved, each with the head it holds.
 */
export async function branchReviews(gate, client, artefact) {
  const r = await api(gate, `/api/tasks?kind=${RANGE_KIND}&brief=1`);
  const same = (t) => t.assignee === client.from && t.input?.repo === artefact.repo && (t.input?.branch ?? null) === (artefact.branch ?? null);
  const mine = (r?.tasks ?? []).filter(same);
  const headOf = (t) => (t.artefact ?? t.output)?.head ?? t.input?.head ?? null;
  return {
    open: mine.find((t) => t.state === "review_requested" || (t.state === "in_progress" && lastDecision(t)?.kind === "reject")) ?? null,
    approved: mine.filter((t) => t.state === "completed").map((t) => ({ task: t, head: headOf(t) })),
    headOf,
  };
}

/**
 * Open the review of a branch, or carry on the one it has. A branch has one
 * review at a time per agent: the same head as an approved review answers
 * with that review, approved; the same head as the open review answers with
 * it, waiting; a review sent back takes these commits as its revision; and a
 * review still waiting on other commits is left to its reviewers, with
 * `waiting_on` set. Returns the task, its state, whether it was a revision,
 * the digest the reviewers' decisions carry, and `held`, the artefact the
 * review holds when it is not the one given.
 */
export async function proposeRange(client, artefact, { gate = null, taskId = null, review = { rule: "any_one_approves", to: null } } = {}) {
  const digest = await contentHash(artefact);
  if (taskId) return { task_id: taskId, state: await submitForReview(client, taskId, artefact, review), revised: true, digest };
  if (gate) {
    const { open, approved, headOf } = await branchReviews(gate, client, artefact);
    const done = approved.find((a) => a.head === artefact.head);
    if (done) return { task_id: done.task.task_id, state: "completed", revised: false, digest, held: done.task.output ?? done.task.artefact };
    if (open?.state === "review_requested") {
      const same = headOf(open) === artefact.head;
      return { task_id: open.task_id, state: "review_requested", revised: false, digest, held: open.artefact, ...(same ? {} : { waiting_on: headOf(open) }) };
    }
    if (open) return { task_id: open.task_id, state: await submitForReview(client, open.task_id, artefact, review), revised: true, digest };
  }
  const input = { summary: artefact.summary, repo: artefact.repo, branch: artefact.branch, base: artefact.base, head: artefact.head, commits: artefact.commits.length };
  const created = await client.call("task.create", { kind: RANGE_KIND, assignee: client.from, input, review_required: true, idempotency_key: rangeKey(artefact.base, artefact.head) });
  let state = created.state;
  if (state === "created" || state === "in_progress") state = await submitForReview(client, created.task_id, artefact, review);
  return { task_id: created.task_id, state, revised: false, digest };
}

/** A sealed commit's message: the proposed message as it was, a blank line, then the gate's trailers. */
export function sealedMessage(message, pairs) {
  return `${message}\n\n${pairs.map(([token, value]) => `${token}: ${value}`).join("\n")}\n`;
}

/**
 * Seal an approved branch: write each approved commit again on the sealed
 * one before it, the first on the approved base, with its own tree, author
 * and message and the gate's trailers, signed with `signingKey` when one is
 * given or with the committer's own key with `signOwn`; then write the
 * evidence note beside each. Returns the sealed commits, oldest first.
 */
export async function sealRange(repo, { note, policy = null, signingKey = null, signOwn = false, env = {} }) {
  const { approved } = noteArtefacts(note);
  if (!Array.isArray(approved?.commits)) throw new Error(`${note.task_id} holds no commits to seal`);
  const of = approved.commits.length;
  let parent = approved.base ?? null;
  const sealed = [];
  // Signed off by whoever seals it: the committer git records on each commit.
  const signoff = await committerIdent(repo, env);
  for (const [index, c] of approved.commits.entries()) {
    const pairs = await trailersFor(note, policy, { series: { index, of, proposed: c.sha }, signoff });
    const sha = await commitTree(repo, { tree: c.tree, parent, author: c.author, message: sealedMessage(c.message, pairs), signingKey, signOwn, env });
    sealed.push(sha);
    parent = sha;
  }
  // One note for the whole branch: git keeps the text once, and each sealed
  // commit points at it.
  const text = JSON.stringify(note, null, 2) + "\n";
  for (const sha of sealed) await writeNote(repo, sha, text);
  return sealed;
}

/**
 * A commit's place in a sealed branch, from its trailers: { index, of,
 * proposed }, { invalid } when it does not read, or null when it is not one.
 * `proposed` is the commit the reviewers saw, which only the longer form
 * an earlier version wrote names; the place alone finds it in the evidence.
 */
export function seriesOf(trailerList) {
  const approval = approvalOf(trailerList);
  if (!approval || approval.invalid !== undefined || !approval.place) return approval?.invalid !== undefined ? { invalid: approval.invalid } : null;
  if (approval.place.invalid !== undefined) return { invalid: approval.place.invalid };
  return { ...approval.place, proposed: approval.proposed ?? null };
}

/**
 * Check a sealed commit against the branch its note approved: it is the
 * commit its trailers say, at that place; its tree, author and message are
 * those of the commit the reviewers saw, with nothing after the message but
 * the gate's trailers; it sits on the approved base, or on the sealed
 * commit before it; and the patch the reviewers saw for it is git's own
 * diff of the change it makes. Returns a list of problems.
 */
export async function checkRangeCommit(repo, commit, { series, note }) {
  const problems = [];
  const { approved } = noteArtefacts(note);
  const commits = approved?.commits;
  if (series.invalid !== undefined) return [`the commit's place in its branch, ${JSON.stringify(series.invalid)}, does not read as <n>/<of>`];
  if (!Array.isArray(commits) || commits.length !== series.of) return [`the commit says it is one of ${series.of}, and the approved branch holds ${Array.isArray(commits) ? commits.length : "no"} commits`];
  for (const [i, x] of commits.entries()) {
    if ((x.parent ?? null) !== (i === 0 ? (approved.base ?? null) : commits[i - 1].sha)) return ["the approved commits are not one line on their base"];
  }
  const c = commits[series.index];
  if (series.proposed && c.sha !== series.proposed) problems.push(`it says it was proposed as ${short(series.proposed)}, and commit ${series.index + 1} of the approved branch is ${short(c.sha)}`);
  if (commit.tree !== c.tree) problems.push(`its tree is not the tree of ${short(c.sha)}, the commit the reviewers saw`);
  if (c.message.split("\n").some((line) => GATE_TRAILER.test(line))) problems.push(`the message the reviewers saw for ${short(c.sha)} holds a line only the gate writes`);
  const prefix = `${c.message}\n\n`;
  if (!commit.message.startsWith(prefix)) problems.push(`its message is not the message of ${short(c.sha)}, the commit the reviewers saw`);
  else if (!commit.message.slice(prefix.length).replace(/\n+$/, "").split("\n").every((line) => GATE_TRAILER.test(line))) problems.push("lines other than the gate's trailers follow the proposed message");
  const a = commit.author, b = c.author;
  if (!a || !b || a.name !== b.name || a.email !== b.email || a.date !== b.date) problems.push(`its author is not the author of ${short(c.sha)}`);
  if (commit.parents.length > 1) problems.push("it is a merge commit");
  const parent = commit.parents[0] ?? null;
  if (series.index === 0) {
    if (parent !== (approved.base ?? null)) problems.push(`the branch was approved on ${short(approved.base)}, and its first commit sits on ${short(parent)}`);
  } else {
    const prev = parent ? await rawCommit(repo, parent) : null;
    const theirs = prev ? approvalOf(await parseTrailers(repo, prev.message)) : null;
    if (!prev || theirs?.task !== note.task_id || theirs?.place?.index !== series.index - 1 || theirs?.place?.of !== series.of || prev.tree !== commits[series.index - 1].tree) {
      problems.push(`it does not sit on commit ${series.index} of the same approved branch`);
    }
  }
  const parentTree = series.index === 0 ? (approved.base ? await treeOf(repo, approved.base) : await emptyTree(repo)) : commits[series.index - 1].tree;
  const check = await faithfulCheck(repo, parentTree, c.patch);
  if (!check.ok) problems.push(`${short(c.sha)}: ${check.reason}`);
  else if (check.tree !== c.tree) problems.push(`the patch the reviewers saw for ${short(c.sha)} does not give its tree`);
  return problems;
}

export { RANGE_KIND };
