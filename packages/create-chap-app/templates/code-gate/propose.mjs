// Propose the working tree's change for review: `node propose.mjs "what it does" [options]`.
//
//   --repo <path>   the repository (default: the current directory)
//   --by <name>     who drafted it, recorded on the artefact (default: working tree)
//   --task <id>     submit to this task, a revision of a change sent back
//   --wait          stay until the decision is made, and say what it was
//   --commit        with --wait: commit the approved change here, through the hooks
//   --poll <ms>     how often to look while waiting (default 2000)
//
// The change is the patch of the working tree against HEAD, tracked and
// untracked files alike, taken through a temporary index. It opens a task
// as the agent named in chap.config.json, is submitted with task.complete,
// and waits in the desk. An approval lets `git commit` through the hooks;
// an override is applied to the working tree here first, so what is
// committed is what the reviewer approved; a rejection that asks for a
// revision prints the note to act on, and the next proposal with the same
// summary on the same branch goes to that task as the revision.
//
// Exit codes with --wait: 0 approved, 2 rejected, 3 revision requested.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { agentClient, agentSigningKey, describeChange, lastDecision, loadGate, propose, reviewersReady, reviewRule, task, waitForDecision } from "./lib/gate.mjs";
import { applyToWorkingTree, commitAll, patchApplies, repoRoot } from "./lib/git.mjs";

const here = dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const out = { summary: null, repo: process.cwd(), by: "working tree", task: null, wait: false, commit: false, poll: 2000 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") out.repo = argv[++i];
    else if (a === "--by") out.by = argv[++i];
    else if (a === "--task") out.task = argv[++i];
    else if (a === "--wait") out.wait = true;
    else if (a === "--commit") { out.commit = true; out.wait = true; }
    else if (a === "--poll") out.poll = Number(argv[++i]);
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
  const client = await agentClient(gate);
  const artefact = await describeChange(repo, { summary: args.summary, drafted_by: args.by });
  if (!artefact) { log("Nothing to propose: the working tree matches HEAD."); return 0; }
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
  log(`  ${artefact.files.length} file${artefact.files.length === 1 ? "" : "s"}: ${files}`);
  log(`  digest ${digest}`);
  log(`  review at ${gate.base}/#task=${encodeURIComponent(task_id)}`);
  if (!args.wait) return 0;

  if (state === "review_requested") log("Waiting for the decision at the desk.");
  const view = await waitForDecision(gate, task_id, { pollMs: args.poll });
  const decision = lastDecision(view);
  if (view.state === "completed") {
    if (decision?.kind === "override") {
      const changed = await syncOverride(repo, artefact, view.output);
      log(`Approved with an edit by ${decision.reviewer}${decision.comment ? `: ${decision.comment}` : ""}.${changed ? " The reviewer's version is now in the working tree." : ""}`);
    } else {
      log(`Approved by ${decision?.reviewer ?? "a reviewer"}${decision?.comment ? `: ${decision.comment}` : ""}.`);
    }
    if (args.commit) {
      const signing = await agentSigningKey(gate);
      const sha = await commitAll(repo, `${artefact.summary}\n`, { signingKey: signing.key });
      log(`Committed as ${sha.slice(0, 12)} with the CHAP trailers and the evidence note${signing.key ? ", signed with the agent's key" : `; unsigned: ${signing.reason}`}.`);
    } else {
      log("Commit it with: git commit -am \"...\" (the hooks add the trailers and the note).");
    }
    return 0;
  }
  if (view.state === "declined") {
    log(`Rejected by ${decision?.reviewer ?? "a reviewer"}${decision?.comment ? `: ${decision.comment}` : ""}. Nothing to commit.`);
    return 2;
  }
  if (view.state === "in_progress") {
    log(`Revision requested by ${decision?.reviewer ?? "a reviewer"}: ${decision?.comment ?? "no note"}`);
    log("Revise the working tree and propose again; the same task carries on.");
    return 3;
  }
  log(`The task is ${view.state}; nothing to commit.`);
  return 2;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  main().then((code) => process.exit(code)).catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}

export { task };
