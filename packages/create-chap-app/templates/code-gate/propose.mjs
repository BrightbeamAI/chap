// Propose the working tree's change for review: `node propose.mjs "what it does" [options]`.
//
//   --repo <path>   the repository (default: the current directory)
//   --by <name>     who drafted it, recorded on the artefact (default: working tree)
//   --model <name>  the model that wrote it, for example "Claude Opus 5.5",
//                   where the session does not name it: CHAP_MODEL names the
//                   session's model when Claude Code's SessionStart hook sets it
//                   (install-hooks.mjs --claude)
//   --context <file> a note for the reviewer, in Markdown ("-" reads standard input):
//                   what was asked, what changed and why, how it was tested
//   --task <id>     submit to this task, a revision of a change sent back
//   --wait          stay until the decision is made, and say what it was
//   --commit        with --wait: commit the approved change here, through the hooks
//   --timeout <min> with --wait: stop waiting after this many minutes with exit code 4
//   --no-open       leave the browser alone (CHAP_NO_BROWSER=1 does the same)
//   --poll <ms>     how often to look while waiting (default 2000)
//
// The change is the patch of the working tree against HEAD, tracked and
// untracked files alike, taken through a temporary index. It opens a task
// as the agent named in chap.config.json, is submitted with task.complete,
// and waits in the desk. An approval lets `git commit` through the hooks;
// an override is applied to the working tree here first, so what is
// committed is what the reviewer approved; a rejection that asks for a
// revision prints the review as a prompt to act on, and the next proposal
// with the same summary on the same branch goes to that task as the
// revision, with what changed since the reviewer looked. The gate is
// started in the background when it is not running on this machine, and
// the review is brought to the reviewer's screen.
//
// Exit codes with --wait: 0 approved, 2 rejected, 3 revision requested,
// 4 still waiting when --timeout ran out.

import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { agentClient, changeSince, commitSigning, describeChange, ensureGate, gateEnv, lastDecision, loadGate, propose, reviewersReady, reviewRule, revisionTarget, task } from "./lib/gate.mjs";
import { announce, commandAgain, printPrompt, promptAfter, readContext, reviewerName, waitWithin, withNote } from "./lib/loop.mjs";
import { MODEL_SOURCES, sessionModel } from "./lib/model.mjs";
import { submittedArtefact } from "./desk/followup.js";
import { applyToWorkingTree, commitAll, patchApplies, repoRoot } from "./lib/git.mjs";

const here = dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const out = { summary: null, repo: process.cwd(), by: "working tree", model: null, context: null, task: null, wait: false, commit: false, poll: 2000, timeout: null, open: true };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") out.repo = argv[++i];
    else if (a === "--by") out.by = argv[++i];
    else if (a === "--model") out.model = argv[++i];
    else if (a === "--task") out.task = argv[++i];
    else if (a === "--wait") out.wait = true;
    else if (a === "--commit") { out.commit = true; out.wait = true; }
    else if (a === "--poll") out.poll = Number(argv[++i]);
    else if (a === "--context") out.context = argv[++i];
    else if (a === "--timeout") { out.timeout = Number(argv[++i]); if (!(out.timeout > 0)) throw new Error("--timeout takes a number of minutes"); }
    else if (a === "--no-open") out.open = false;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}`);
    else if (out.summary === null) out.summary = a;
    else throw new Error(`Unexpected argument ${a}`);
  }
  if (!out.summary) throw new Error('Say what the change does: node propose.mjs "summary"');
  return out;
}

/**
 * Bring the working tree to the artefact the reviewer approved after an
 * override: the proposed patch comes out, the approved one goes in.
 */
export async function syncOverride(repo, proposed, approved) {
  if (proposed.patch === approved.patch) return false;
  if (!(await patchApplies(repo, proposed.patch, { reverse: true }))) {
    throw new Error("The working tree no longer holds the proposed change, so the reviewer's edit cannot be applied here. Apply the approved patch by hand.");
  }
  await applyToWorkingTree(repo, proposed.patch, { reverse: true });
  await applyToWorkingTree(repo, approved.patch);
  return true;
}

export async function main(argv = process.argv.slice(2), { log = console.log, gateDir = here } = {}) {
  const args = parseArgs(argv);
  const repo = await repoRoot(args.repo);
  if (!repo) throw new Error(`${args.repo} is not inside a git repository`);
  const gate = await loadGate(gateDir);
  await ensureGate(gate, { log });
  const client = await agentClient(gate);
  const again = commandAgain(join(here, "propose.mjs"), argv);
  // The model the commit names: the session's, from CHAP_MODEL, else the agent's word, --model.
  const session = sessionModel({ flag: args.model });
  if (session.differs) log(`--model says ${session.differs}, and the session runs ${session.model}: the session's model is the one named.`);
  const artefact = await describeChange(repo, { summary: args.summary, drafted_by: args.by, model: session.model, model_source: session.source, context: await readContext(args.context) });
  if (!artefact) { log("Nothing to propose: the working tree matches HEAD."); return 0; }
  // Sent back, and the working tree still holds the change reviewed: the
  // review is what the agent needs, so it is given again.
  const sentBack = args.task ? null : await revisionTarget(gate, client, artefact);
  const reviewed = sentBack ? submittedArtefact(sentBack) : null;
  if (reviewed && reviewed.patch === artefact.patch) {
    log(`Changes were requested on ${sentBack.task_id}, and the working tree still holds the change reviewed. Revise it as the review asks, then run the same command again.`);
    printPrompt(await promptAfter(gate, sentBack.task_id, again), log);
    return 3;
  }
  // A revision shows the reviewer what changed since they sent it back.
  const since = reviewed ? await changeSince(repo, reviewed, artefact) : null;
  if (since) artefact.since = since;
  if (!artefact.model) log('No model is named: the session gives none, so Drafted-by names the agent. Pass --model, or see "The model that wrote it" in the README.');
  const review = reviewRule(gate);
  let ready = await reviewersReady(client, review);
  if (!ready.ok) {
    const advice = `${review.rule} needs ${ready.need} reviewer${ready.need === 1 ? "" : "s"} in the workspace and ${ready.have} ${ready.have === 1 ? "has" : "have"} joined, so the review cannot open yet. A reviewer joins by opening the desk at ${gate.base}/`;
    if (!args.wait) throw new Error(`${advice}; propose again after that.`);
    log(`${advice}; waiting for that.`);
    while (!(ready = await reviewersReady(client, review)).ok) await new Promise((r) => setTimeout(r, args.poll));
  }
  const { task_id, state, revised, digest } = await propose(client, artefact, { gate, taskId: args.task, review });
  const files = artefact.files.map((f) => `${f.path} (+${f.added ?? "bin"} -${f.removed ?? "bin"})`).join(", ");
  log(`${revised ? "Revised" : "Proposed as"} ${task_id} (${state}): ${artefact.summary}`);
  log(`  ${artefact.files.length} file${artefact.files.length === 1 ? "" : "s"}: ${files}${artefact.model ? `, written by ${artefact.model}${MODEL_SOURCES[artefact.model_source] ? ` (${MODEL_SOURCES[artefact.model_source]})` : ""}` : ""}`);
  log(`  digest ${digest}`);
  if (state === "review_requested") await announce(gate, task_id, { open: args.open, log });
  else log(`  review at ${gate.base}/#task=${encodeURIComponent(task_id)}`);
  if (!args.wait) return 0;

  if (state === "review_requested") log(`Waiting for the decision at the desk${args.timeout ? `, for up to ${args.timeout} min` : ""}.`);
  const view = await waitWithin(gate, task_id, { pollMs: args.poll, timeoutMinutes: args.timeout });
  if (!view) {
    log(`Still waiting for the review of ${task_id}. Run the same command again to keep waiting:`);
    log(`  ${again}`);
    return 4;
  }
  const decision = lastDecision(view);
  if (view.state === "completed") {
    if (decision?.kind === "override") {
      const changed = await syncOverride(repo, artefact, view.output);
      log(`${withNote(`Approved with an edit by ${reviewerName(gate, decision.reviewer)}`, decision.rationale ?? decision.comment)}${changed ? " The reviewer's version is now in the working tree." : ""}`);
      printPrompt(await promptAfter(gate, task_id, again), log);
    } else {
      log(withNote(`Approved by ${reviewerName(gate, decision?.reviewer)}`, decision?.comment));
    }
    if (args.commit) {
      const signing = await commitSigning(gate, repo);
      const sha = await commitAll(repo, `${artefact.summary}\n`, { signingKey: signing.key, noSign: signing.noSign, env: gateEnv(gate) });
      log(`Committed as ${sha.slice(0, 12)} with its trailers and the evidence note, ${signing.describe}.`);
    } else {
      log("Commit it with: git commit -am \"...\" (the hooks add the trailers and the note).");
    }
    return 0;
  }
  if (view.state === "declined") {
    log(`${withNote(`Rejected by ${reviewerName(gate, decision?.reviewer)}`, decision?.comment)} Nothing to commit.`);
    printPrompt(await promptAfter(gate, task_id, again), log);
    return 2;
  }
  if (view.state === "in_progress") {
    log(`Changes requested by ${reviewerName(gate, decision?.reviewer)}. Revise the working tree and run the same command again; the same task carries on.`);
    printPrompt(await promptAfter(gate, task_id, again), log);
    return 3;
  }
  log(`The task is ${view.state}; nothing to commit.`);
  return 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}

export { task };
