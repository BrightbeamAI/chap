// What the gate refuses: the ways an unapproved change could try to land,
// from the independent review of the gate. Each one is set up the way an
// agent with its own key, or a person with push access, would set it up.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { generateSigner, makeClient } from "../desk/chap-client.mjs";
import { parsePatch } from "../desk/diff.js";
import { generateKeyFile, opensshPublicKey, sshKeyFiles } from "../keys.mjs";
import { changeKey, checkChange, contentHash, describeChange, propose, reviewerClient } from "../lib/gate.mjs";
import { emptyTree, git, head, treeOf, writeNote } from "../lib/git.mjs";
import { verifyRange } from "../verify.mjs";
import { buildTrust } from "../trust.mjs";
import { readTasks, run as runAgent } from "../agent.mjs";
import { appendTo, commit, decide, demoRepo, hookEnv, projectDir, reviewsFor, run, startGate } from "./helpers.mjs";

let g, human, trustFile;

before(async () => {
  g = await startGate({ suffix: "attacks" });
  human = await g.reviewer(g.config.humans[0].uri);
  const { trust } = await buildTrust(g.gate, { keyPath: g.keyPath });
  trustFile = join(await mkdtemp(join(tmpdir(), "chap-trust-")), "chap-trust.json");
  await writeFile(trustFile, JSON.stringify(trust));
});

after(async () => { await g.close(); });

/** Open a task as the agent and submit `artefact` the way the gate's own tools do, or with review.request to `to`. */
async function submit(artefact, { to = null, client = g.agent } = {}) {
  const t = await client.call("task.create", { kind: "code_change", assignee: client.from, input: { summary: artefact.summary }, review_required: true, idempotency_key: changeKey(artefact.base, artefact.patch) + Math.random() });
  if (to) await client.call("review.request", { task_id: t.task_id, artefact, to, rule: "any_one_approves" });
  else await client.call("task.complete", { task_id: t.task_id, output: artefact });
  return t.task_id;
}

test("an agent that addresses a review to itself and approves it commits nothing", async () => {
  const repo = await demoRepo();
  await appendTo(repo, "lib/calc.mjs", "\nexport const BACKDOOR = true;\n");
  const artefact = await describeChange(repo, { summary: "harmless", drafted_by: "x" });
  for (const to of [[g.agent.from], [`workspace:${g.config.workspace}`]]) {
    const id = await submit(artefact, { to });
    const d = await g.agent.call("decide.approve", { task_id: id, approved_artefact_digest: await contentHash(artefact) });
    assert.equal(d.state, "completed", "the coordinator accepts the agent's approval of a review addressed to itself");
  }
  await assert.rejects(commit(g, repo, "harmless"), (e) => /is an agent/.test(e.stderr) && !/approved as/.test(e.stderr));
  assert.equal((await git(repo, ["status", "--porcelain"])).trim().length > 0, true, "nothing was committed");
});

test("a participant that joined as a human under a URI the gate does not name counts for nothing", async () => {
  const repo = await demoRepo();
  await appendTo(repo, "lib/calc.mjs", "\nexport const ALSO_BAD = true;\n");
  const artefact = await describeChange(repo, { summary: "harmless too", drafted_by: "x" });
  const fake = await g.reviewer("human:evil@local");
  const id = await submit(artefact, { to: [fake.from] });
  assert.equal((await fake.call("decide.approve", { task_id: id, approved_artefact_digest: await contentHash(artefact) })).state, "completed");
  await assert.rejects(commit(g, repo, "harmless too"), (e) => /human:evil@local approved the change, and is not a reviewer/.test(e.stderr));
});

test("a patch that shows one file and applies another is refused in the desk, at the commit and by the verifier", async () => {
  const repo = await demoRepo();
  const base = await head(repo);
  const hidden = [
    "--- a/lib/calc.mjs", "+++ b/lib/calc.mjs", "@@ -1,4 +1,4 @@", "-// A calculator library, the subject of the gate's sample tasks.", "+// A calculator library. Nothing to see here.", " ", " export function add(a, b) {", "   return a + b;",
    "diff --git a/README.md b/README.md", "--- a/README.md", "+++ b/README.md", "@@ -1,3 +1,3 @@", "-# calc", "+# calc!", " ", " A small calculator library used to show the CHAP code gate.", "",
  ].join("\n");
  assert.ok(parsePatch(hidden).anomalies.some((a) => /text before the first file/.test(a)));
  const misnamed = ["diff --git a/README.md b/README.md", "--- a/package.json", "+++ b/package.json", "@@ -1 +1 @@", "-{", "+[", ""].join("\n");
  assert.ok(parsePatch(misnamed).anomalies.some((a) => /name a\/package\.json/.test(a)));
  // Applied by git to the base, the hidden patch changes both files; the desk would show one.
  const artefact = { summary: "README tweak", repo: "r", branch: "main", base, files: [{ path: "README.md", added: 1, removed: 1 }], patch: hidden, drafted_by: "x" };
  const id = await submit(artefact);
  const review = (await reviewsFor(g, human)).find((r) => r.task_id === id);
  await decide(human, "decide.approve", review);
  const problems = await checkChange(repo, { parent: base, parentTree: await treeOf(repo, base), tree: "0".repeat(40), approved: artefact });
  assert.match(problems.join(), /what a reviewer sees of the patch is not what git applies: text before the first file/);
  await git(repo, ["apply", "-"], { input: hidden });
  await assert.rejects(commit(g, repo, "README tweak"), (e) => /no approved change matches/.test(e.stderr));
});

test("a note signed with keys the trust policy does not pin fails, offline and online", async () => {
  const repo = await demoRepo();
  await appendTo(repo, "README.md", "\nForged.\n");
  await commit(g, repo, "forged", { CHAP_GATE: "off" });
  const sha = await head(repo);
  const parent = (await git(repo, ["rev-parse", "HEAD~1"])).trim();
  await git(repo, ["checkout", "-q", "HEAD~1"]);
  await appendTo(repo, "README.md", "\nForged.\n");
  const artefact = await describeChange(repo, { summary: "forged", drafted_by: "x" });
  await git(repo, ["checkout", "-q", "--", "."]);
  await git(repo, ["checkout", "-q", "main"]);
  // Fresh keys under the real names, and a note built from them.
  const fakeHuman = await generateSigner(human.from);
  const fakeAgent = await generateSigner(g.config.agent.uri);
  const taskId = "tsk_FORGED";
  const submission = await fakeAgent.sign({ jsonrpc: "2.0", id: "s", method: "task.complete", params: { workspace: g.config.workspace, from: fakeAgent.uri, task_id: taskId, output: artefact } });
  const approval = await fakeHuman.sign({ jsonrpc: "2.0", id: "d", method: "decide.approve", params: { workspace: g.config.workspace, from: fakeHuman.uri, task_id: taskId, approved_artefact_digest: await contentHash(artefact) } });
  const note = {
    chap_note: 1, workspace: g.config.workspace, coordinator: g.gate.url, task_id: taskId, kind: "code_change", agent: fakeAgent.uri, summary: "forged", rule: "any_one_approves", requested_to: [human.from],
    submission: { method: "task.complete", envelope: submission }, decision: { method: "decide.approve", reviewer: human.from },
    decisions: [{ method: "decide.approve", reviewer: human.from, envelope: approval }],
    approved_artefact: artefact, proposed_artefact: artefact, decision_envelope: approval,
    reviewer_keys: { [human.from]: [fakeHuman.publicJwk] }, agent_keys: [fakeAgent.publicJwk],
  };
  const msg = `forged\n\nCHAP-Workspace: ${g.config.workspace}\nCHAP-Task: ${taskId}\nCHAP-Agent: ${fakeAgent.uri}\nCHAP-Reviewer: ${human.from}\nCHAP-Decision: approve\nCHAP-Artefact: ${await contentHash(artefact)}\n`;
  const tree = (await git(repo, ["rev-parse", `${sha}^{tree}`])).trim();
  const forged = (await git(repo, ["commit-tree", tree, "-p", parent, "-F", "-"], { input: msg })).trim();
  await writeNote(repo, forged, JSON.stringify(note));
  const offline = await verifyRange(repo, forged, { trust: trustFile });
  assert.equal(offline[0].status, "FAIL");
  assert.match(offline[0].detail, /signature of human:you@local/);
  assert.match(offline[0].detail, /agent's signature on its submission/);
  const live = await verifyRange(repo, forged, { gate: g.gate });
  assert.equal(live[0].status, "FAIL");
  assert.match(live[0].detail, /does not know tsk_FORGED/);
});

test("an approval made against one commit does not land on another", async () => {
  const repo = await demoRepo();
  const base = await head(repo);
  await git(repo, ["checkout", "-q", "-b", "agent/x"]);
  await appendTo(repo, "lib/calc.mjs", "\nexport const ONE = 1;\n");
  const artefact = await describeChange(repo, { summary: "Add ONE", drafted_by: "x" });
  const { task_id } = await propose(g.agent, artefact, { gate: g.gate });
  await decide(human, "decide.approve", (await reviewsFor(g, human)).find((r) => r.task_id === task_id));
  await git(repo, ["checkout", "-q", "--", "."]);
  await git(repo, ["checkout", "-q", "main"]);
  await appendTo(repo, "README.md", "\nmain moved on\n");
  await commit(g, repo, "moved", { CHAP_GATE: "off" });
  assert.notEqual(await head(repo), base);
  await appendTo(repo, "lib/calc.mjs", "\nexport const ONE = 1;\n");
  await assert.rejects(commit(g, repo, "Add ONE"), (e) => /no approved change matches what is staged on this commit's parent/.test(e.stderr));
  const problems = await checkChange(repo, { parent: await head(repo), parentTree: await treeOf(repo, "HEAD"), tree: "x", approved: artefact });
  assert.match(problems.join(), /approved against .*propose it again on this base/);
});

test("a merge commit fails unless it is a clean merge, and a person's own signed commit passes only when the policy lists them", async () => {
  const repo = await demoRepo();
  await git(repo, ["checkout", "-q", "-b", "side"]);
  await appendTo(repo, "README.md", "\nside\n");
  await commit(g, repo, "side", { CHAP_GATE: "off" });
  await git(repo, ["checkout", "-q", "main"]);
  await appendTo(repo, "lib/calc.mjs", "\nexport const EXTRA = 1;\n");
  await git(repo, ["add", "-A"]);
  const tree = (await git(repo, ["write-tree"])).trim();
  const merge = (await git(repo, ["commit-tree", tree, "-p", "HEAD", "-p", "side", "-m", "Merge"])).trim();
  const results = await verifyRange(repo, merge, { trust: trustFile, requireSignedCommit: true });
  assert.equal(results[0].status, "FAIL");
  assert.match(results[0].detail, /a merge commit/);
  // A person's commit, signed with their own SSH key, passes with --allow-people when the policy lists them.
  await git(repo, ["reset", "-q", "--hard"]);
  const dir = await mkdtemp(join(tmpdir(), "chap-person-"));
  const { path } = await generateKeyFile("alice@example.com", dir);
  const ssh = await sshKeyFiles("alice@example.com", path);
  await appendTo(repo, "README.md", "\nby a person\n");
  await git(repo, ["add", "-A"]);
  await run("git", ["-c", "gpg.format=ssh", "-c", `user.signingkey=${ssh.privatePath}`, "commit", "-q", "-S", "-m", "by a person"], { cwd: repo, env: { ...hookEnv(g), CHAP_GATE: "off" } });
  const person = await readFile(ssh.allowedSigners, "utf8");
  const trust = { ...JSON.parse(await readFile(trustFile, "utf8")), people: person.trim().split("\n") };
  const withPeople = join(dir, "chap-trust.json");
  await writeFile(withPeople, JSON.stringify(trust));
  assert.equal((await verifyRange(repo, "HEAD", { trust: withPeople, allowPeople: true }))[0].status, "ok");
  assert.equal((await verifyRange(repo, "HEAD", { trust: trustFile, allowPeople: true }))[0].status, "FAIL", "not listed, not passed");
  // The agent's own key does not pass a commit without an approval.
  const agentSsh = await sshKeyFiles(g.config.agent.uri, g.keyPath);
  await appendTo(repo, "README.md", "\nby the agent, unapproved\n");
  await git(repo, ["add", "-A"]);
  await run("git", ["-c", "gpg.format=ssh", "-c", `user.signingkey=${agentSsh.privatePath}`, "commit", "-q", "-S", "-m", "agent"], { cwd: repo, env: { ...hookEnv(g), CHAP_GATE: "off" } });
  assert.equal((await verifyRange(repo, "HEAD", { trust: withPeople, allowPeople: true }))[0].status, "FAIL");
});

test("an agent restarted with its change still waiting carries on, and refuses a working tree with anything else", async () => {
  const repo = await demoRepo();
  const source = join(await mkdtemp(join(tmpdir(), "chap-tasks-")), "tasks.csv");
  await writeFile(source, "title,brief,files\nValidate the inputs of add,\"Make add throw a TypeError.\",lib/calc.mjs\n");
  // What a first run leaves behind when it stops while the change waits:
  // the task under its key, the draft in the working tree, the draft submitted.
  const [row] = await readTasks(source);
  await git(repo, ["checkout", "-q", "-b", "agent/r"]);
  const created = await g.agent.call("task.create", { kind: "code_change", assignee: g.agent.from, review_required: true, idempotency_key: row.key, input: { summary: row.title, brief: row.brief } });
  await appendTo(repo, "lib/calc.mjs", "\n// waiting for review\n");
  await propose(g.agent, await describeChange(repo, { summary: row.title, drafted_by: "scripted" }), { taskId: created.task_id });
  const lines = [];
  const running = runAgent({ source, repo, branch: "agent/r", once: true, pollMs: 50, log: (l) => lines.push(l), keyPath: g.keyPath, gate: g.gate });
  const review = await (async () => { for (let i = 0; i < 200; i++) { const r = (await reviewsFor(g, human)).find((x) => x.task_id === created.task_id); if (r) return r; await new Promise((r2) => setTimeout(r2, 50)); } })();
  await decide(human, "decide.approve", review);
  assert.deepEqual(Object.values(await running), ["approve"]);
  assert.match(await readFile(join(repo, "lib/calc.mjs"), "utf8"), /waiting for review/);
  assert.equal((await git(repo, ["status", "--porcelain"])).trim(), "");
  await appendTo(repo, "README.md", "\nunrelated\n");
  await assert.rejects(runAgent({ source, repo, branch: "agent/r", once: true, pollMs: 50, log: () => {}, keyPath: g.keyPath, gate: g.gate }), /uncommitted changes that are not a change waiting for review/);
});

test("in a linked worktree the hooks keep their state in the git directory", async () => {
  const repo = await demoRepo();
  const wt = join(await mkdtemp(join(tmpdir(), "chap-wt-")), "wt");
  await git(repo, ["worktree", "add", "-q", "-b", "wt", wt]);
  await appendTo(wt, "lib/calc.mjs", "\nexport const IN_WORKTREE = 1;\n");
  const artefact = await describeChange(wt, { summary: "In a worktree", drafted_by: "x" });
  const { task_id } = await propose(g.agent, artefact, { gate: g.gate });
  await decide(human, "decide.approve", (await reviewsFor(g, human)).find((r) => r.task_id === task_id));
  await commit(g, wt, "In a worktree");
  assert.equal((await git(wt, ["status", "--porcelain"])).trim(), "", "no approval file left in the working tree");
  assert.equal((await verifyRange(wt, "HEAD", { gate: g.gate }))[0].status, "ok");
});
