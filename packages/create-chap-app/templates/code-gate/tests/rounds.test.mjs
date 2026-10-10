// The second review's findings, held: an approval counts only in the round
// it was made in, a pull request sits on its base and reuses no approval
// from it, the contents the desk showed are the repository's, attributes do
// not decide what is binary, the signed workspace is the policy's, and a
// trust file is checked before it is believed.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { basename, dirname, join } from "node:path";
import { generateSigner } from "../desk/chap-client.mjs";
import { hasInvisible } from "../desk/render.js";
import { buildNote, checkApproval, checkChange, describeChange, onlinePolicy, policyFromTrust, propose } from "../lib/gate.mjs";
import { commitInfo, faithfulCheck, git, head, readNote, treeOf, writeNote } from "../lib/git.mjs";
import { verifyRange } from "../verify.mjs";
import { applyAnswer, safePath } from "../agent.mjs";
import { appendTo, commit, decide, demoRepo, hookEnv, projectDir, reviewsFor, run, startGate } from "./helpers.mjs";

let g, alice, bob, carol;

before(async () => {
  g = await startGate({ suffix: "rounds", humans: ["human:alice@local", "human:bob@local", "human:carol@local"], review: { rule: "quorum:2" } });
  alice = await g.reviewer("human:alice@local");
  bob = await g.reviewer("human:bob@local");
  carol = await g.reviewer("human:carol@local");
});

after(async () => { await g.close(); });

test("approvals from two rounds of the same artefact do not make a quorum", async () => {
  const repo = await demoRepo({ hooks: false });
  await appendTo(repo, "lib/calc.mjs", "\nexport const TWICE = 2;\n");
  const artefact = await describeChange(repo, { summary: "Add TWICE", drafted_by: "x" });
  const { task_id } = await propose(g.agent, artefact, { gate: g.gate });
  const round1 = (await reviewsFor(g, alice)).find((r) => r.task_id === task_id);
  await decide(alice, "decide.approve", round1);
  await decide(bob, "decide.reject", round1, { comment: "again", request_revision: true });
  await propose(g.agent, artefact, { gate: g.gate, taskId: task_id });
  const round2 = (await reviewsFor(g, carol)).find((r) => r.task_id === task_id);
  assert.notEqual(round2.submission.seq, round1.submission.seq);
  await decide(carol, "decide.approve", round2);
  assert.equal((await decide(bob, "decide.reject", round2, { comment: "no" })).state, "declined");
  // A note claiming alice's round-one approval and carol's round-two approval.
  const ev = await (await fetch(`${g.base}/api/tasks/${task_id}/evidence`)).json();
  const approvals = ev.decisions.filter((d) => d.envelope.method === "decide.approve");
  const submission = ev.submissions.at(-1);
  const note = {
    chap_note: 1, workspace: ev.workspace, task_id, kind: "code_change", agent: ev.task.assignee, rule: "quorum:2", requested_to: [],
    submission: { method: submission.envelope.method, envelope: submission.envelope },
    decision: { method: "decide.approve", reviewer: carol.from }, decisions: approvals.map((d) => ({ method: d.envelope.method, reviewer: d.envelope.params.from, envelope: d.envelope })),
    approved_artefact: artefact, proposed_artefact: artefact, decision_envelope: approvals.at(-1).envelope,
  };
  const problems = await checkApproval(note, onlinePolicy(g.gate, ev));
  assert.match(problems.join(), /the approval by human:alice@local does not name the submission of this review round/);
  assert.match(problems.join(), /quorum:2 needs 2 approvals .*; 1 on record/);
});

test("a pull request is one line from its base, and reuses no approval the base's history used", async () => {
  const repo = await demoRepo();
  const base0 = await head(repo);
  const land = async (summary) => {
    const p = await propose(g.agent, await describeChange(repo, { summary, drafted_by: "x" }), { gate: g.gate });
    const r = (await reviewsFor(g, alice)).find((x) => x.task_id === p.task_id);
    await decide(alice, "decide.approve", r);
    await decide(bob, "decide.approve", r);
    await commit(g, repo, summary);
    return head(repo);
  };
  // An approved change lands on main, and an approved revert follows it.
  await appendTo(repo, "lib/calc.mjs", "\nexport const DEBUG_ENDPOINT = true;\n");
  const c1 = await land("Add a debug endpoint");
  await git(repo, ["checkout", "-q", base0, "--", "lib/calc.mjs"]);
  const c2 = await land("Remove the debug endpoint");
  // The first change comes back on the old base, with its message and note.
  const c3 = (await git(repo, ["commit-tree", (await commitInfo(repo, c1)).tree, "-p", base0, "-F", "-"], { input: (await commitInfo(repo, c1)).message })).trim();
  await writeNote(repo, c3, await readNote(repo, c1));
  const replay = await verifyRange(repo, `${c2}..${c3}`, { gate: g.gate, base: c2 });
  assert.equal(replay[0].status, "FAIL");
  assert.match(replay[0].detail, /was used already by .* in the base's history/);
  // A new change approved on the old base passes as it is: it is behind main, and merges.
  await git(repo, ["checkout", "-q", "-b", "behind", base0]);
  await appendTo(repo, "README.md", "\nBehind main.\n");
  const c4 = await land("Note the README");
  const behind = await verifyRange(repo, `${c2}..${c4}`, { gate: g.gate, base: c2 });
  assert.equal(behind.length, 1);
  assert.equal(behind[0].status, "ok", behind[0].detail);
  // Bringing main into the pull request with a merge does not pass.
  await git(repo, ["merge", "-q", "--no-verify", "--no-edit", "main"]);
  const merged = await verifyRange(repo, `${c2}..HEAD`, { gate: g.gate, base: c2 });
  assert.ok(merged.length >= 2);
  assert.ok(merged.every((r) => r.status === "FAIL"));
  assert.match(merged[0].detail, /a pull request is one line of commits from where it leaves/);
});

test("the contents the desk showed must be the repository's", async () => {
  const repo = await demoRepo({ hooks: false });
  await appendTo(repo, "lib/calc.mjs", "\nexport const SHOWN = 1;\n");
  const artefact = await describeChange(repo, { summary: "Shown", drafted_by: "x" });
  const parent = await head(repo);
  const parentTree = await treeOf(repo, parent);
  const { tree } = await faithfulCheck(repo, parentTree, artefact.patch);
  assert.deepEqual(await checkChange(repo, { parent, parentTree, tree, approved: artefact }), []);
  const faked = { ...artefact, files: artefact.files.map((f) => ({ ...f, before: f.before.replace("multiply", "multiply // reviewed by security") })) };
  assert.match((await checkChange(repo, { parent, parentTree, tree, approved: faked })).join(), /lib\/calc\.mjs the desk showed as it was is not the file at the parent/);
  const twice = { ...artefact, files: [...artefact.files, artefact.files[0]] };
  assert.match((await checkChange(repo, { parent, parentTree, tree, approved: twice })).join(), /lists lib\/calc\.mjs twice/);
});

test("a .gitattributes in the change decides nothing about what is binary", async () => {
  const repo = await demoRepo({ hooks: false });
  await writeFile(join(repo, ".gitattributes"), "*.mjs binary\n");
  await appendTo(repo, "lib/calc.mjs", "\nexport const HIDDEN = true;\n");
  const artefact = await describeChange(repo, { summary: "Attributes", drafted_by: "x" });
  assert.match(artefact.patch, /\+export const HIDDEN = true;/, "the .mjs change is shown as text");
  assert.doesNotMatch(artefact.patch, /Binary files|GIT binary patch/);
  // The patch git writes with the attribute in force, which hides the .mjs change.
  await git(repo, ["add", "-A"]);
  const hidden = await git(repo, ["diff", "--cached", "--binary", "--full-index", "--no-renames"]);
  assert.match(hidden, /GIT binary patch/);
  const check = await faithfulCheck(repo, await treeOf(repo, "HEAD"), hidden);
  assert.equal(check.ok, false);
  assert.match(check.reason, /lib\/calc\.mjs is shown as binary, and its content is text/);
});

test("a decision signed in another workspace, a trust file with a prototype name or an unknown rule, and the paths an agent may not write", async () => {
  const repo = await demoRepo({ hooks: false });
  await appendTo(repo, "README.md", "\nElsewhere.\n");
  const { task_id } = await propose(g.agent, await describeChange(repo, { summary: "Elsewhere", drafted_by: "x" }), { gate: g.gate });
  const review = (await reviewsFor(g, alice)).find((r) => r.task_id === task_id);
  await decide(alice, "decide.approve", review);
  await decide(bob, "decide.approve", review);
  const ev = await (await fetch(`${g.base}/api/tasks/${task_id}/evidence`)).json();
  const note = buildNote(ev, g.gate.url);
  assert.deepEqual(await checkApproval(note, onlinePolicy(g.gate, ev)), []);
  const other = { ...onlinePolicy(g.gate, ev), workspace: "wsp_somewhere_else" };
  assert.match((await checkApproval(note, other)).join(), /was made in .*, and the gate at .* covers wsp_somewhere_else/);
  const key = (await generateSigner("human:x")).publicJwk;
  assert.throws(() => policyFromTrust({ chap_trust: 1, rule: "quorum:2", reviewers: JSON.parse('{"__proto__": []}'), agents: {} }, "t"), /not a list|not a participant URI/);
  assert.throws(() => policyFromTrust({ chap_trust: 1, rule: "quorum:2", reviewers: { constructor: [key] }, agents: {} }, "t"), /not a participant URI/);
  assert.throws(() => policyFromTrust({ chap_trust: 1, rule: "Quorum:2", reviewers: {}, agents: {} }, "t"), /rule is "Quorum:2"/);
  assert.throws(() => policyFromTrust({ chap_trust: 1, reviewers: { "human:a": [] }, agents: {} }, "t"), /not a list of Ed25519 public keys/);
  for (const bad of [".git.", ".git ", "GIT~1/config", "a/.Git./x", "c:x"]) assert.equal(safePath(bad), false, bad);
  assert.equal(hasInvisible("ok\r"), false);
  assert.equal(hasInvisible("a\u202Eb"), true);
  assert.equal(hasInvisible("a\rb"), true);
});

test("commit-msg fails closed when pre-commit recorded nothing, and an agent writes nothing into the gate", async () => {
  const repo = await demoRepo();
  const message = join(repo, ".git", "TEST_MSG");
  await writeFile(message, "Made without pre-commit\n");
  await assert.rejects(
    run(process.execPath, [join(projectDir, "gate-hook.mjs"), "commit-msg", message], { cwd: repo, env: hookEnv(g) }),
    (e) => e.code === 1 && /pre-commit recorded no approval/.test(e.stderr),
  );
  assert.equal(await readFile(message, "utf8"), "Made without pre-commit\n");
  // A repository that holds the gate project: the agent may not write in it.
  const gateDir = projectDir.replace(/\/$/, "");
  const name = basename(gateDir);
  await assert.rejects(applyAnswer(dirname(gateDir), { files: [{ path: `${name}/never-written.txt`, content: "x" }], delete: [] }), /inside the gate project/);
  await assert.rejects(applyAnswer(dirname(gateDir), { files: [], delete: [`${name}/gate-hook.mjs`] }), /inside the gate project/);
});
