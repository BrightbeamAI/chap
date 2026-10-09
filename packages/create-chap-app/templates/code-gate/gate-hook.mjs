// The git hooks, in one file: `node gate-hook.mjs <pre-commit|commit-msg|post-commit>`.
//
// pre-commit   refuses the commit unless the staged change is exactly an
//              artefact the gate holds as approved: the approved patch
//              applied to the commit's parent gives the tree being
//              committed. The match is written to .git/chap/approval.json
//              for the two hooks after it.
// commit-msg   adds the CHAP trailers for that approval to the message.
// post-commit  writes the evidence beside the commit as a note under
//              refs/notes/chap: the artefact as proposed and as approved,
//              the signed decision as the chain holds it, the reviewer's
//              keys and the chain head.
//
// CHAP_GATE=off lets a commit through with no approval and no trailers; a
// verifier run with its default policy fails such a commit, so the bypass
// is visible where it matters. CHAP_URL names the gate when it is not at
// the address in chap.config.json.

import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { approvedChanges, buildNote, contentHash, evidence, loadGate, served, trailersFor } from "./lib/gate.mjs";
import { addTrailersToFile, emptyTree, git, head, stagedPatch, stagedTree, treeAfterPatch, treeOf, writeNote } from "./lib/git.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const say = (line) => process.stderr.write(`chap: ${line}\n`);

async function approvalFile(repo) {
  const path = (await git(repo, ["rev-parse", "--git-path", "chap/approval.json"])).trim();
  return join(repo, path);
}

/**
 * The approved change whose patch produces the staged tree, or null. The
 * patch text is compared first, then the trees, so a patch git would print
 * differently still matches when it makes the same change.
 */
export async function matchApproval(gate, repo, { patch, parentTree, tree }) {
  const digest = await contentHash(patch);
  const candidates = await approvedChanges(gate);
  for (const t of candidates) {
    const approvedPatch = t.output?.patch;
    if (typeof approvedPatch !== "string") continue;
    if ((await contentHash(approvedPatch)) === digest) return t;
  }
  for (const t of candidates.slice(0, 25)) {
    const approvedPatch = t.output?.patch;
    if (typeof approvedPatch !== "string") continue;
    try {
      if ((await treeAfterPatch(repo, parentTree, approvedPatch)) === tree) return t;
    } catch { /* does not apply to this parent */ }
  }
  return null;
}

async function preCommit(repo, gate) {
  const patch = await stagedPatch(repo);
  if (!patch.trim()) { say("nothing staged, nothing to gate"); return 0; }
  await served(gate);
  const parentTree = (await head(repo)) ? await treeOf(repo, "HEAD") : await emptyTree(repo);
  const tree = await stagedTree(repo);
  const match = await matchApproval(gate, repo, { patch, parentTree, tree });
  if (!match) {
    say("no approved change matches what is staged.");
    say(`propose it from the repository with: node ${join(here, "propose.mjs")} "what the change does"`);
    say(`then decide at ${gate.base}/ and commit again. CHAP_GATE=off commits without an approval.`);
    return 1;
  }
  const decision = match.review?.decisions?.at(-1);
  const file = await approvalFile(repo);
  await mkdir(dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify({ task_id: match.task_id, digest: await contentHash(match.output), decision: decision?.kind, reviewer: decision?.reviewer }));
  say(`approved as ${match.task_id} by ${decision?.reviewer ?? "a reviewer"} (${decision?.kind ?? "approve"})`);
  return 0;
}

async function commitMsg(repo, gate, messageFile) {
  const file = await approvalFile(repo);
  let approval;
  try { approval = JSON.parse(await readFile(file, "utf8")); } catch { say("no approval recorded by pre-commit; the message gets no trailers"); return 0; }
  const ev = await evidence(gate, approval.task_id);
  if (!ev) { say(`the gate no longer knows ${approval.task_id}`); return 1; }
  const note = buildNote(ev, gate.url);
  await addTrailersToFile(repo, messageFile, await trailersFor(note));
  return 0;
}

async function postCommit(repo, gate) {
  const file = await approvalFile(repo);
  let approval;
  try { approval = JSON.parse(await readFile(file, "utf8")); } catch { return 0; }
  await rm(file, { force: true });
  const sha = await head(repo);
  const ev = await evidence(gate, approval.task_id);
  if (!ev) { say(`the gate no longer knows ${approval.task_id}; no note written`); return 0; }
  const note = buildNote(ev, gate.url);
  await writeNote(repo, sha, JSON.stringify(note, null, 2) + "\n");
  say(`${sha.slice(0, 12)} carries ${approval.task_id}; the evidence is in refs/notes/chap. Push it with: git push origin refs/notes/chap`);
  return 0;
}

export async function hook(stage, argv, repo = process.cwd()) {
  if (process.env.CHAP_GATE === "off") {
    if (stage === "pre-commit") say("CHAP_GATE=off: this commit carries no approval");
    return 0;
  }
  const gate = await loadGate(here);
  if (stage === "pre-commit") return preCommit(repo, gate);
  if (stage === "commit-msg") return commitMsg(repo, gate, argv[0]);
  if (stage === "post-commit") return postCommit(repo, gate);
  say(`unknown hook ${stage}`);
  return 1;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  hook(process.argv[2], process.argv.slice(3)).then((code) => process.exit(code)).catch((e) => {
    say(e instanceof Error ? e.message : String(e));
    process.exit(1);
  });
}

