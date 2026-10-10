// The code gate, end to end, in-process: a coordinator with signatures
// required and the chain on, a demo repository under the gate's hooks, a
// change proposed as a patch, the hooks refusing an unapproved commit and
// letting an approved one through with its trailers, its note and its
// signature, the verifier online and offline against a trust file, an
// override applied before the commit, the built-in agent taking the sample
// tasks through the gate, a restart, the report and the evidence.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { jsonPatch } from "../desk/chap-client.mjs";
import { readKeyFile } from "../keys.mjs";
import { applyJsonPatch, buildNote, checkApproval, contentHash, describeChange, envelopeVerifies, fingerprint, onlinePolicy, propose } from "../lib/gate.mjs";
import { commitInfo, git, head, parseTrailers, rawCommit, readNote } from "../lib/git.mjs";
import { syncOverride } from "../propose.mjs";
import { verifyRange } from "../verify.mjs";
import { summarise } from "../report.mjs";
import { buildTrust } from "../trust.mjs";
import { parseAnswer, parseCsv, run as runAgent, scriptedDraft, buildPrompt, contextFiles, safePath } from "../agent.mjs";
import { appendTo, commit, decide, demoRepo, projectDir, reviewsFor, run, startGate, until } from "./helpers.mjs";

let g, human, repo;

before(async () => {
  g = await startGate({ extra: { sign_commits: "agent" } });
  human = await g.reviewer(g.config.humans[0].uri);
  repo = await demoRepo();
});

after(async () => { await g.close(); });

const online = () => ({ gate: g.gate });

test("the gate runs the production shape, and the demo repository is under its hooks", async () => {
  assert.equal(g.config.require_signatures, true);
  assert.equal(g.config.chain, true);
  assert.equal(g.config.mode, "trial");
  const cfg = await (await fetch(`${g.base}/api/config`)).json();
  assert.ok(cfg.profiles.includes("security-signed/1.0") && cfg.profiles.includes("modes/1.0"));
  assert.equal((await git(repo, ["config", "--get", "core.hooksPath"])).trim(), join(projectDir, "hooks"));
});

test("a change in the working tree is proposed as a canonical patch that waits in the desk", async () => {
  await appendTo(repo, "lib/calc.mjs", "\nexport function square(a) {\n  return a * a;\n}\n");
  const artefact = await describeChange(repo, { summary: "Add square", drafted_by: "the test" });
  assert.equal(artefact.branch, "main");
  assert.equal(artefact.base, await head(repo));
  assert.deepEqual(artefact.files.map((f) => [f.path, f.added, f.removed]), [["lib/calc.mjs", 4, 0]]);
  assert.match(artefact.files[0].before, /export function multiply/);
  assert.match(artefact.files[0].after, /export function square/);
  assert.match(artefact.patch, /^diff --git a\/lib\/calc\.mjs b\/lib\/calc\.mjs\nindex [0-9a-f]{40}\.\.[0-9a-f]{40} 100644\n/);
  const first = await propose(g.agent, artefact, { gate: g.gate });
  assert.equal(first.state, "review_requested");
  const again = await propose(g.agent, artefact, { gate: g.gate });
  assert.equal(again.task_id, first.task_id, "the same change is the same task");
  const review = (await reviewsFor(g, human)).find((r) => r.task_id === first.task_id);
  assert.equal(review.artefact.patch, artefact.patch);
});

test("the hooks refuse a commit with no approval, then commit the approved change with its trailers and note", async () => {
  await assert.rejects(commit(g, repo, "Add square"), (e) => /no approved change matches/.test(e.stderr));
  const review = (await reviewsFor(g, human)).find((r) => r.input.summary === "Add square");
  assert.equal((await decide(human, "decide.approve", review, { comment: "fine" })).state, "completed");
  await commit(g, repo, "Add square");
  const sha = await head(repo);
  const info = await commitInfo(repo, sha);
  const trailers = Object.fromEntries((await parseTrailers(repo, info.message)).map((t) => [t.token, t.value]));
  // Who drafted it, who approved it by name and email, who signed it off
  // (the committer), and the approval the evidence note holds; no more.
  const me = g.config.humans.find((h) => h.uri === human.from);
  const committer = (await rawCommit(repo, sha)).committer;
  assert.deepEqual(Object.keys(trailers).sort(), ["CHAP-Approval", "Drafted-by", "Reviewed-by", "Signed-off-by"]);
  assert.equal(trailers["CHAP-Approval"], review.task_id);
  assert.equal(trailers["Reviewed-by"], `${me.display_name} <${me.email}>`);
  assert.equal(trailers["Signed-off-by"], `${committer.name} <${committer.email}>`);
  assert.equal(trailers["Drafted-by"], g.config.agent.uri, "with no model named, the agent");
  const note = JSON.parse(await readNote(repo, sha));
  // The rest of the record is in the note.
  assert.equal(note.rule, "any_one_approves");
  assert.equal(note.workspace, g.config.workspace);
  assert.ok(note.coordinator && note.chain_head);
  assert.equal(await contentHash(note.approved_artefact), await contentHash(review.artefact));
  assert.equal(note.reviewer_keys[human.from][0].x, human.signer.publicJwk.x, `the key behind ${fingerprint(human.signer.publicJwk)}`);
  assert.equal(note.submission.envelope.method, "task.complete");
  assert.match(note.submission.envelope.sig, /^ed25519:/);
  assert.equal(envelopeVerifies(note.decision_envelope, note.reviewer_keys[human.from]).ok, true);
  assert.equal(note.agent_keys[0].kid, (await readKeyFile(g.config.agent.uri, g.keyPath)).kid);
  const results = await verifyRange(repo, "HEAD", online());
  assert.equal(results[0].status, "ok", results[0].detail);
  assert.match(results[0].detail, /checked against the gate/);
  // The same approval does not commit twice.
  await git(repo, ["checkout", "-q", "-b", "again", "HEAD~1"]);
  await appendTo(repo, "lib/calc.mjs", "\nexport function square(a) {\n  return a * a;\n}\n");
  await assert.rejects(commit(g, repo, "Add square again"), (e) => /was committed already/.test(e.stderr));
  await git(repo, ["checkout", "-q", "--", "."]);
  await git(repo, ["checkout", "-q", "main"]);
});

test("offline, against a trust file the team commits, and with the gate stopped", async () => {
  const { trust, missing } = await buildTrust(g.gate, { keyPath: g.keyPath });
  assert.deepEqual(missing, []);
  assert.deepEqual(Object.keys(trust.reviewers), [human.from]);
  assert.deepEqual(Object.keys(trust.agents), [g.config.agent.uri]);
  const file = join(await mkdtemp(join(tmpdir(), "chap-trust-")), "chap-trust.json");
  await writeFile(file, JSON.stringify(trust));
  const results = await verifyRange(repo, "HEAD", { trust: file });
  assert.equal(results[0].status, "ok", results[0].detail);
  assert.match(results[0].detail, /checked against .*chap-trust\.json/);
  // A trust file that pins another reviewer's key: the same commit fails.
  const other = await g.reviewer("human:someone@local");
  const wrong = { ...trust, reviewers: { [human.from]: [other.signer.publicJwk] } };
  await writeFile(file, JSON.stringify(wrong));
  const failed = await verifyRange(repo, "HEAD", { trust: file });
  assert.equal(failed[0].status, "FAIL");
  assert.match(failed[0].detail, /signature of human:you@local/);
});

test("an override is applied to the working tree before the commit, and verifies as approved with an edit", async () => {
  await appendTo(repo, "lib/calc.mjs", "\nexport function negate(a) {\n  return -a;\n}\n");
  const artefact = await describeChange(repo, { summary: "Add negate", drafted_by: "the test" });
  const { task_id } = await propose(g.agent, artefact, { gate: g.gate });
  const review = (await reviewsFor(g, human)).find((r) => r.task_id === task_id);
  const { rewritePatch } = await import("../desk/diff.js");
  const file = review.artefact.files[0];
  const editedAfter = file.after.replace("return -a;", "return 0 - a;");
  const patch = rewritePatch(review.artefact.patch, { [file.path]: editedAfter }, { [file.path]: file.before });
  const edited = { ...review.artefact, patch, files: [{ ...file, after: editedAfter }] };
  const decided = await decide(human, "decide.override", review, { rationale: "spell it out", diff: jsonPatch(review.artefact, edited), intent_preserved: true });
  assert.equal(decided.state, "completed");
  assert.ok(await syncOverride(repo, review.artefact, decided.applied));
  assert.match(await readFile(join(repo, "lib/calc.mjs"), "utf8"), /return 0 - a;/);
  await commit(g, repo, "Add negate");
  const results = await verifyRange(repo, "HEAD", online());
  assert.equal(results[0].status, "ok", results[0].detail);
  assert.match(results[0].detail, /approved with an edit/);
  const note = JSON.parse(await readNote(repo, await head(repo)));
  assert.deepEqual(applyJsonPatch(note.proposed_artefact, note.decision_envelope.params.diff), note.approved_artefact);
});

test("the built-in agent takes tasks.csv through the gate: an approval, a revision then an override, and a rejection", async () => {
  const branch = "agent/test";
  const lines = [];
  const running = runAgent({ source: join(projectDir, "tasks.csv"), repo, branch, once: true, pollMs: 50, log: (l) => lines.push(l), gateDir: projectDir, keyPath: g.keyPath, gate: g.gate });
  const first = await until(async () => (await reviewsFor(g, human)).find((r) => r.input.summary === "Validate the inputs of add"));
  await decide(human, "decide.approve", first, { comment: "good" });
  const second = await until(async () => (await reviewsFor(g, human)).find((r) => r.input.summary === "Add a subtract function"));
  await decide(human, "decide.reject", second, { comment: "name the parameters minuend and subtrahend", request_revision: true });
  const revised = await until(async () => (await reviewsFor(g, human)).find((r) => r.task_id === second.task_id && r.artefact.patch.includes("Revision: name the parameters")));
  const { rewritePatch } = await import("../desk/diff.js");
  const calc = revised.artefact.files.find((f) => f.path === "lib/calc.mjs");
  const after = calc.after.replace("  return a - b;", "  return a - b; // minuend less subtrahend");
  const patch = rewritePatch(revised.artefact.patch, { [calc.path]: after }, { [calc.path]: calc.before });
  const edited = { ...revised.artefact, patch, files: revised.artefact.files.map((f) => (f.path === calc.path ? { ...f, after } : f)) };
  await decide(human, "decide.override", revised, { rationale: "say which is which", diff: jsonPatch(revised.artefact, edited), intent_preserved: true });
  const third = await until(async () => (await reviewsFor(g, human)).find((r) => r.input.summary === "Document the library"));
  await decide(human, "decide.reject", third, { comment: "not this week" });
  const outcomes = await running;
  assert.deepEqual(Object.values(outcomes), ["approve", "override", "reject"]);
  const results = await verifyRange(repo, `main..${branch}`, { ...online(), requireSignedCommit: true });
  assert.deepEqual(results.map((r) => r.status), ["ok", "ok"], JSON.stringify(results));
  for (const r of results) assert.match(r.detail, /commit signed by the agent's key/);
  assert.match(await git(repo, ["show", `${branch}:lib/calc.mjs`]), /minuend less subtrahend/);
  assert.equal((await git(repo, ["status", "--porcelain"])).trim(), "", "the rejected change was taken out of the working tree");
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  delete env.NODE_OPTIONS;
  const { stdout } = await run("node", ["--test", "--test-reporter=tap"], { cwd: repo, env });
  assert.match(stdout, /# pass 4/);
});

test("a restarted agent commits nothing twice and opens no new task", async () => {
  const before = (await (await fetch(`${g.base}/api/tasks`)).json()).tasks.length;
  const lines = [];
  const outcomes = await runAgent({ source: join(projectDir, "tasks.csv"), repo, branch: "agent/test", once: true, pollMs: 50, log: (l) => lines.push(l), gateDir: projectDir, keyPath: g.keyPath, gate: g.gate });
  assert.deepEqual(Object.values(outcomes), ["approve", "override", "reject"]);
  assert.equal(lines.filter((l) => l.includes("committed earlier")).length, 2);
  assert.equal((await (await fetch(`${g.base}/api/tasks`)).json()).tasks.length, before);
});

test("the report counts what the gate recorded, by model and by file, and lists the reviewers' words", async () => {
  const tasks = (await (await fetch(`${g.base}/api/tasks?kind=code_change`)).json()).tasks;
  const s = summarise(tasks);
  assert.equal(s.total, 5);
  assert.equal(s.approved, 2);
  assert.equal(s.overridden, 2);
  assert.equal(s.rejected, 1);
  assert.equal(s.revisions, 1);
  assert.ok(s.override_rationales.some((o) => o.text === "say which is which"));
  assert.ok(s.rejection_notes.some((r) => r.text === "name the parameters minuend and subtrahend" && r.revision));
  const scripted = s.models.find((m) => m.model === "scripted drafter (no model)");
  assert.deepEqual([scripted.changes, scripted.approve, scripted.override, scripted.reject, scripted.sent_back], [3, 1, 1, 1, 1]);
  assert.ok(s.files.find((f) => f.path === "lib/calc.mjs").edited >= 2);
});

test("the evidence holds the submissions, the decisions, and every member's type and keys", async () => {
  const tasks = (await (await fetch(`${g.base}/api/tasks?kind=code_change&state=completed`)).json()).tasks;
  const ev = await (await fetch(`${g.base}/api/tasks/${tasks[0].task_id}/evidence`)).json();
  assert.ok(ev.submissions.length >= 1);
  assert.equal(ev.members[human.from].type, "human");
  assert.equal(ev.members[g.config.agent.uri].type, "agent");
  assert.deepEqual(await checkApproval(buildNote(ev, g.gate.url), onlinePolicy(g.gate, ev)), []);
  assert.equal((await fetch(`${g.base}/api/tasks/nope/evidence`)).status, 404);
  assert.equal((await fetch(`${g.base}/api/tasks/${tasks[0].task_id}/evidence/more`)).status, 404);
  assert.equal((await fetch(`${g.base}/api/tasks/%E0%A4%A`)).status, 400);
  assert.equal((await fetch(`${g.base}/api/tasks?limit=abc`)).status, 400);
});

test("the pieces: the CSV, a model's answer, the paths an agent may write, the prompt, and RFC 6902", async () => {
  assert.deepEqual(parseCsv('title,brief\n"A, b","say ""hi"""\n'), [{ title: "A, b", brief: 'say "hi"' }]);
  assert.deepEqual(parseAnswer('```json\n{"summary":"s","files":[{"path":"a.txt","content":"x"}],"delete":["b.txt"]}\n```').delete, ["b.txt"]);
  for (const bad of ["../x", "/etc/passwd", ".git/config", ".GIT/hooks/pre-commit", "a/.Git/x", "a\\b", "a//b"]) {
    assert.equal(safePath(bad), false, bad);
    assert.throws(() => parseAnswer(JSON.stringify({ files: [{ path: bad, content: "" }] })), /may not write/, bad);
  }
  assert.equal(safePath(".github/workflows/ci.yml"), true);
  assert.throws(() => parseAnswer("no json here"), /no JSON/);
  const context = await contextFiles(repo, { title: "Document the library", brief: "README.md should say", files: ["README.md"] });
  assert.equal(context.shown[0].path, "README.md");
  const answer = parseAnswer(scriptedDraft(buildPrompt({ title: "Document the library", brief: "the README", files: [] }, context)));
  assert.equal(answer.files[0].path, "README.md");
  assert.deepEqual(applyJsonPatch({ a: 1, b: [1, 2] }, [{ op: "replace", path: "/a", value: 2 }, { op: "add", path: "/b/-", value: 3 }, { op: "remove", path: "/b/0" }]), { a: 2, b: [2, 3] });
  assert.throws(() => applyJsonPatch({}, [{ op: "move", from: "/a", path: "/b" }]), /Unsupported/);
  assert.throws(() => applyJsonPatch({}, [{ op: "add", path: "/__proto__/x", value: 1 }]), /Refused/);
  assert.throws(() => applyJsonPatch({}, [{ op: "replace", path: "/toString", value: 1 }]), /not found/);
});
