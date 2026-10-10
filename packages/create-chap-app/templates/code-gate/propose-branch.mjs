// Review a branch before it is pushed: `node propose-branch.mjs [<base>..]<head> [options]`.
//
//   <base>..<head>     the commits to review: those on <head> since it left <base>'s
//                      history (default: the current branch since its upstream)
//   --repo <path>      the repository (default: the current directory)
//   --by <name>        the agent or tool that made the commits, as the desk shows it
//   --model <name>     the model that wrote them, as each commit will name it,
//                      for example "Claude Opus 5.5" (default: CHAP_MODEL)
//   --summary <text>   one line for the review (default: from the commits)
//   --context <file>   a note for the reviewer, in Markdown ("-" reads it from standard
//                      input): what was asked, what changed and why, how it was tested,
//                      what to look at closely. The desk shows it above the commits.
//   --task <id>        submit to this task, a revision of a branch sent back
//   --wait             stay until the decision is made, then seal an approved branch
//   --push <remote>    with --wait: push the sealed branch to <remote> through the
//                      pre-push hook, then the evidence notes
//   --to <branch>      the branch to push to (default: the branch's own name)
//   --timeout <min>    with --wait: stop waiting after this many minutes with exit code 4,
//                      so an agent whose tool calls have a time limit runs it again
//   --no-open          leave the browser alone (CHAP_NO_BROWSER=1 does the same)
//   --poll <ms>        how often to look while waiting (default 2000)
//
// For agents that commit on their own. The commits are proposed as one
// review: the desk shows each with its message, author, files and diff.
// Once the reviewers approve, the branch is sealed: each commit is written
// again with the same tree, author and message, followed by the gate's
// trailers (Drafted-by with the model that wrote it, Reviewed-by with each
// approver's name and email, and CHAP-Approval linking it to the evidence),
// signed as sign_commits in chap.config.json says, with the evidence as a
// note. The branch then points at
// the sealed commits, which are the ones the pre-push hook and verify.mjs
// accept.
//
// The gate is started in the background when it is not running on this
// machine, and the review is brought to the reviewer: an open desk shows it,
// and with none open the browser opens at it. When the reviewers ask for
// changes or reject the branch, the command prints their decision as a
// prompt for the agent: their note, their comments on lines with the code
// each is about, and the command to run again.
//
// The same command, run again, carries on: it waits while the review is
// open, seals the commits once they are approved, sends amended commits to
// a review sent back for a revision (with what changed since the reviewers
// last looked), and pushes a branch sealed already. Commits at the start of
// the range that are approved already are left as they are, and the review
// covers the ones after them.
//
// Exit codes with --wait: 0 approved and sealed (and pushed with --push),
// 2 rejected, 3 revision requested, 4 still waiting when --timeout ran out,
// 1 anything that went wrong.

import { readFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { agentClient, api, approvalOf, buildNote, checkApproval, commitSigning, contentHash, ensureGate, evidence, gateEnv, lastDecision, loadGate, onlinePolicy, reviewersReady, reviewRule, strictestRule, trustAtRef } from "./lib/gate.mjs";
import { git, parseTrailers, rawCommit, repoRoot } from "./lib/git.mjs";
import { announce, commandAgain, printPrompt, promptAfter, readContext, reviewerName, waitWithin, withNote } from "./lib/loop.mjs";
import { branchReviews, describeRange, proposeRange, sealRange, sinceLastReview } from "./lib/range.mjs";
import { pushNotes } from "./push-notes.mjs";
import { verifyCommit } from "./verify.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const short = (sha) => (sha ? String(sha).slice(0, 12) : "none");
const quoted = (path) => (/[\s"'$`\\]/.test(path) ? JSON.stringify(path) : path);

export function parseArgs(argv) {
  const out = { range: null, repo: process.cwd(), by: null, model: process.env.CHAP_MODEL || null, summary: null, context: null, task: null, wait: false, push: null, to: null, poll: 2000, timeout: null, open: true, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const value = () => { const v = argv[++i]; if (v === undefined || v.startsWith("--")) throw new Error(`${a} needs a value`); return v; };
    if (a === "--repo") out.repo = value();
    else if (a === "--by") out.by = value();
    else if (a === "--model") out.model = value() || null;
    else if (a === "--summary") out.summary = value();
    else if (a === "--context") out.context = value();
    else if (a === "--timeout") { out.timeout = Number(value()); if (!(out.timeout > 0)) throw new Error("--timeout takes a number of minutes"); }
    else if (a === "--no-open") out.open = false;
    else if (a === "--task") out.task = value();
    else if (a === "--wait") out.wait = true;
    else if (a === "--poll") out.poll = Number(value());
    else if (a === "--push") { out.push = value(); out.wait = true; }
    else if (a === "--to") out.to = value();
    else if (a === "--help" || a === "-h") out.help = true;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}. node propose-branch.mjs --help lists them.`);
    else if (out.range === null) out.range = a;
    else throw new Error(`Unexpected argument ${a}`);
  }
  if (out.to && !out.push) throw new Error("--to names where --push pushes to; give --push <remote> with it");
  return out;
}

/** A revision as a commit, or a clear error. */
async function commitOf(repo, rev) {
  const sha = await git(repo, ["rev-parse", "--verify", "--quiet", `${rev}^{commit}`]).then((o) => o.trim(), () => "");
  if (!sha) throw new Error(`${rev} is not a commit in ${repo}`);
  return sha;
}

/** The base and head a range names, the base defaulting to the head's upstream. */
async function resolveRange(repo, range) {
  let base = null, head = range ?? "HEAD";
  if (range?.includes("..")) [base, head] = range.split(/\.\.\.?/);
  head ||= "HEAD";
  if (!base) {
    base = await git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", `${head}@{upstream}`]).then((o) => o.trim(), () => "");
    if (!base) throw new Error(`${head} has no upstream to review it against. Name the base: node propose-branch.mjs origin/main..${head}`);
  }
  await commitOf(repo, base);
  await commitOf(repo, head);
  return { base, head };
}

/** The local branch a head names: the head itself, or the branch HEAD is on; null for a detached head. */
async function branchOf(repo, head) {
  if (head !== "HEAD") {
    const ok = await git(repo, ["show-ref", "--verify", "--quiet", `refs/heads/${head}`]).then(() => true, () => false);
    return ok ? head : null;
  }
  return git(repo, ["symbolic-ref", "--quiet", "--short", "HEAD"]).then((o) => o.trim() || null, () => null);
}

/**
 * Where the review starts: the commits of base..head that hold already as
 * approved commits, from the first, are left as they are. Returns the
 * commit the review starts after (base when none hold) and whether all of
 * them hold.
 */
async function approvedPrefix(repo, gate, cfg, base, head) {
  const fork = (await git(repo, ["merge-base", await commitOf(repo, base), await commitOf(repo, head)]).catch(() => "")).trim() || null;
  const shas = (await git(repo, ["rev-list", "--reverse", fork ? `${fork}..${head}` : head])).split("\n").filter(Boolean);
  const pinned = fork ? await trustAtRef(repo, fork) : null;
  const seen = new Map();
  let kept = 0;
  for (const sha of shas) {
    const r = await verifyCommit(repo, sha, { policy: pinned, gate: pinned ? null : gate, seen, allowUnsigned: !cfg.require_signatures });
    if (r.status !== "ok") break;
    kept++;
  }
  // An approved branch is kept whole or not at all: one cut short (its last
  // commits amended, say) is proposed again from its first commit.
  const branches = new Map();
  for (const [i, sha] of shas.slice(0, kept).entries()) {
    const approval = approvalOf(await parseTrailers(repo, (await rawCommit(repo, sha)).message.replace(/\s+$/, "")));
    const place = approval?.place;
    if (!place || place.invalid !== undefined) continue;
    const task = approval.task;
    const entry = branches.get(task) ?? { of: place.of, count: 0, first: i };
    entry.count++;
    branches.set(task, entry);
  }
  for (const entry of branches.values()) if (entry.count < entry.of) kept = Math.min(kept, entry.first);
  return { start: kept ? shas[kept - 1] : fork, all: shas.length > 0 && kept === shas.length, kept };
}

async function push(repo, gate, remote, dest, sha, log) {
  log(`Pushing ${short(sha)} to ${remote} as ${dest}; the pre-push hook checks every commit.`);
  await git(repo, ["push", remote, `${sha}:refs/heads/${dest}`], { env: gateEnv(gate) });
  await pushNotes(repo, { remote, log: (line) => log(line) });
  log(`Pushed to ${remote} ${dest}, with the evidence notes.`);
}

export async function main(argv = process.argv.slice(2), { log = console.log, gateDir = here } = {}) {
  const args = parseArgs(argv);
  if (args.help) {
    const text = await readFile(fileURLToPath(import.meta.url), "utf8");
    log(text.split("\n").filter((l) => l.startsWith("//")).map((l) => l.replace(/^\/\/ ?/, "")).join("\n"));
    return 0;
  }
  const repo = await repoRoot(args.repo);
  if (!repo) throw new Error(`${args.repo} is not inside a git repository`);
  const gate = await loadGate(gateDir);
  const cfg = await ensureGate(gate, { log });
  const context = await readContext(args.context);
  const again = commandAgain(join(here, "propose-branch.mjs"), argv);
  const { base, head } = await resolveRange(repo, args.range);
  const branch = await branchOf(repo, head);
  const dest = args.to ?? branch;
  if (args.push && !dest) throw new Error(`${head} is not a local branch, so name where to push: --push ${args.push} --to <branch>`);

  const prefix = await approvedPrefix(repo, gate, cfg, base, head);
  if (prefix.all) {
    log(`Every commit of ${base}..${head} is approved already, sealed or committed through the gate.`);
    if (args.push) await push(repo, gate, args.push, dest, await commitOf(repo, head), log);
    return 0;
  }
  if (prefix.kept) log(`${prefix.kept === 1 ? "The first commit is" : `The first ${prefix.kept} commits are`} approved already and ${prefix.kept === 1 ? "stays as it is" : "stay as they are"}; the review covers the commits after ${short(prefix.start)}.`);

  const client = await agentClient(gate);
  const headSha = await commitOf(repo, head);
  const { open: underway, headOf } = await branchReviews(gate, client, { repo: basename(repo), branch });
  // Sent back, and these are still the commits the reviewers saw: the
  // review is what the agent needs, so it is given again.
  if (underway?.state === "in_progress" && headOf(underway) === headSha) {
    log(`Changes were requested on ${underway.task_id}, and the branch still holds the commits reviewed. Amend them as the review asks, then run the same command again.`);
    printPrompt(await promptAfter(gate, underway.task_id, again), log);
    return 3;
  }
  // A revision shows the reviewers what changed since they last looked.
  const since = underway?.state === "in_progress" ? await sinceLastReview(repo, headOf(underway), headSha) : null;
  const artefact = await describeRange(repo, { base: prefix.start ?? base, head, summary: args.summary, drafted_by: args.by ?? gate.config.agent?.display_name ?? client.from, model: args.model, branch, context, since });
  if (!artefact.model) log("No --model was given, so the commits will not name the model that wrote them.");
  const size = Buffer.byteLength(JSON.stringify(artefact));
  const limit = cfg.max_envelope_bytes ?? 1_048_576;
  if (size > limit - 64 * 1024) throw new Error(`The branch's commits come to ${(size / 1_048_576).toFixed(1)} MB, more than the gate takes in one review (${(limit / 1_048_576).toFixed(1)} MB, max_envelope_bytes in chap.config.json). Review it in parts: ${base}..<a commit part way>, then the rest.`);

  const review = reviewRule(gate);
  let ready = await reviewersReady(client, review);
  if (!ready.ok) {
    const advice = `${review.rule} needs ${ready.need} reviewer${ready.need === 1 ? "" : "s"} in the workspace and ${ready.have} ${ready.have === 1 ? "has" : "have"} joined. A reviewer joins by opening the desk at ${gate.base}/`;
    if (!args.wait) throw new Error(`${advice}; propose again after that.`);
    log(`${advice}; waiting for that.`);
    while (!(ready = await reviewersReady(client, review)).ok) await new Promise((r) => setTimeout(r, args.poll));
  }
  const proposal = await proposeRange(client, artefact, { gate, taskId: args.task, review });
  const { task_id, state, revised } = proposal;
  if (proposal.waiting_on) {
    log(`${task_id}, the review of this branch's earlier commits (up to ${short(proposal.waiting_on)}), is still open, and these commits (up to ${short(artefact.head)}) are not in it.`);
    log("Let the reviewers decide it, or ask them to request changes; then run this again and the newer commits go to the same review.");
    return 1;
  }
  // The review may hold these commits as an earlier run proposed them.
  let held = artefact;
  if (proposal.held) {
    const full = await api(gate, `/api/tasks/${encodeURIComponent(task_id)}`);
    held = full?.artefact ?? full?.output ?? artefact;
    if ((await contentHash(held)) !== proposal.digest) log(`The review holds these commits as they were first proposed${held.model ? `, written by ${held.model}` : ", with no model named"}; --model and --summary apply to a new proposal.`);
  }
  const added = held.commits.reduce((n, c) => n + c.files.reduce((m, f) => m + (f.added ?? 0), 0), 0);
  const removed = held.commits.reduce((n, c) => n + c.files.reduce((m, f) => m + (f.removed ?? 0), 0), 0);
  log(`${revised ? "Revised" : state === "completed" ? "Approved already as" : "Proposed as"} ${task_id} (${state}): ${held.summary}`);
  log(`  ${held.commits.length} commit${held.commits.length === 1 ? "" : "s"}${branch ? ` on ${branch}` : ""} since ${short(held.base)}, +${added} -${removed}${held.model ? `, written by ${held.model}` : ""}`);
  for (const c of held.commits) log(`    ${short(c.sha)}  ${c.message.split("\n")[0]}`);
  if (state === "review_requested") await announce(gate, task_id, { open: args.open, log });
  else log(`  review at ${gate.base}/#task=${encodeURIComponent(task_id)}`);
  if (!args.wait) {
    log(`${state === "completed" ? "Run the same command with --wait to seal it." : "Run the same command with --wait to seal the branch once it is approved."}`);
    return 0;
  }

  if (state === "review_requested") log(`Waiting for the decision at the desk${args.timeout ? `, for up to ${args.timeout} min` : ""}.`);
  const view = state === "completed" ? await api(gate, `/api/tasks/${encodeURIComponent(task_id)}`) : await waitWithin(gate, task_id, { pollMs: args.poll, timeoutMinutes: args.timeout });
  if (!view) {
    log(`Still waiting for the review of ${task_id}. Run the same command again to keep waiting:`);
    log(`  ${again}`);
    return 4;
  }
  const decision = lastDecision(view);
  if (view.state === "declined") {
    log(`${withNote(`Rejected by ${reviewerName(gate, decision?.reviewer)}`, decision?.comment)} Nothing is sealed or pushed.`);
    printPrompt(await promptAfter(gate, task_id, again), log);
    return 2;
  }
  if (view.state === "in_progress") {
    log(`Changes requested by ${reviewerName(gate, decision?.reviewer)}. Amend the commits, then run the same command again; the same review carries on.`);
    printPrompt(await promptAfter(gate, task_id, again), log);
    return 3;
  }
  if (view.state !== "completed") { log(`The review is ${view.state}; nothing is sealed.`); return 1; }

  // The approval is checked as the hooks and the verifier will check it,
  // under the trust policy at the base when the repository has one.
  const ev = await evidence(gate, task_id);
  const note = buildNote(ev, gate.url);
  const approvedHead = (ev.task.output ?? ev.task.artefact)?.head;
  const pinned = held.base ? await trustAtRef(repo, held.base) : null;
  const policy = pinned ? { ...pinned } : onlinePolicy(gate, ev);
  policy.rule = strictestRule([policy.rule, review.rule], Object.keys(policy.reviewers));
  const problems = await checkApproval(note, policy, { allowUnsigned: !cfg.require_signatures });
  if (problems.length) {
    log(`${task_id} is approved at the gate, and its approval does not hold under ${policy.source}:`);
    for (const p of problems) log(`  ${p}`);
    return 1;
  }
  const approvers = [...new Set(note.decisions.filter((d) => d.method === "decide.approve").map((d) => d.reviewer))];
  log(withNote(`Approved by ${approvers.map((uri) => reviewerName(gate, uri)).join(" and ")}`, decision?.comment));

  const signing = await commitSigning(gate, repo);
  const sealed = await sealRange(repo, { note, policy, signingKey: signing.key, signOwn: signing.signOwn, env: gateEnv(gate) });
  const sealedHead = sealed.at(-1);
  // The branch, or a detached HEAD, moves to the sealed commits only from
  // the commits that were approved; one that moved since is left alone.
  let moved = false;
  const move = async (ref, extra = []) => git(repo, ["update-ref", ...extra, "-m", `chap: seal ${task_id}`, ref, sealedHead, approvedHead]).catch(() => { moved = true; });
  if (branch) await move(`refs/heads/${branch}`);
  else if ((await commitOf(repo, "HEAD").catch(() => "")) === approvedHead) await move("HEAD", ["--no-deref"]);
  else moved = true;
  log(`Sealed ${sealed.length} commit${sealed.length === 1 ? "" : "s"}: ${short(approvedHead)} is now ${short(sealedHead)}${branch && !moved ? ` on ${branch}` : ""}. Each names its reviewers and the model that drafted it, is ${signing.describe}, and has the evidence in refs/notes/chap.`);
  if (moved) {
    log(`${branch ?? "HEAD"} moved since the commits were proposed, so it was left where it is and nothing is pushed. The sealed commits end at ${sealedHead}; bring the branch to them with: git reset --hard ${sealedHead}, or propose the newer commits after them.`);
    return 1;
  }
  if (args.push) await push(repo, gate, args.push, dest, sealedHead, log);
  else log(`Push it with: git push <remote> ${branch ?? `${sealedHead}:refs/heads/<branch>`}, then node ${quoted(join(here, "push-notes.mjs"))}`);
  return 0;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}
