// The code gate, end to end, in-process: a coordinator with signatures
// required and the chain on, a demo repository under the gate's hooks, a
// change proposed as a patch, the hooks refusing an unapproved commit and
// letting an approved one through with its trailers and its evidence note,
// the verifier on good and tampered commits, an override applied before the
// commit, and the built-in agent taking the sample tasks through the gate
// with an approval, a revision, an override and a rejection. The decisions
// here are scripted because this is a test; in the project the decision is
// made in the desk.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { loadConfig, makeCoordinator, makeServer } from "../server.mjs";
import { generateSigner, jsonPatch } from "../desk/chap-client.mjs";
import { generateKeyFile } from "../keys.mjs";
import { createDemoRepo } from "../demo-repo.mjs";
import { agentClient, applyJsonPatch, contentHash, describeChange, envelopeVerifies, loadGate, propose, reviewerClient } from "../lib/gate.mjs";
import { commitInfo, git, head, parseTrailers, readNote, treeOf } from "../lib/git.mjs";
import { syncOverride } from "../propose.mjs";
import { verifyRange } from "../verify.mjs";
import { summarise } from "../report.mjs";
import { parseAnswer, parseCsv, run as runAgent, scriptedDraft, buildPrompt, contextFiles } from "../agent.mjs";

const run = promisify(execFile);
const projectDir = fileURLToPath(new URL("..", import.meta.url));

let config, server, base, gate, keyPath, human, repo;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(read, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await read();
    if (value) return value;
    if (Date.now() > deadline) throw new Error("timed out waiting");
    await sleep(50);
  }
}
const openReviews = async () => (await (await fetch(`${base}/api/reviews?reviewer=${encodeURIComponent(human.from)}`)).json()).reviews;
const decide = (method, review, extra = {}) => contentHash(review.artefact).then((digest) => human.call(method, { task_id: review.task_id, approved_artefact_digest: digest, ...extra }));
const commitIn = (dir, message, env = {}) => run("git", ["commit", "-q", "-am", message], { cwd: dir, env: { ...process.env, ...env } });

before(async () => {
  config = await loadConfig();
  config.store = ":memory:";
  const coord = await makeCoordinator(config);
  server = await makeServer(config, coord);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
  process.env.CHAP_URL = `${base}/chap`;
  const keyDir = await mkdtemp(join(tmpdir(), "chap-keys-"));
  ({ path: keyPath } = await generateKeyFile(config.agent.uri, keyDir));
  process.env.CHAP_AGENT_KEY = keyPath;
  gate = await loadGate(projectDir);
  human = reviewerClient(gate, config.humans[0].uri, await generateSigner(config.humans[0].uri));
  await human.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [human.signer.publicJwk] } });
  repo = await createDemoRepo(await mkdtemp(join(tmpdir(), "chap-repo-")), { log: () => {} });
});

after(async () => {
  await new Promise((r) => server.close(r));
});

test("the gate runs the production shape, and the demo repository is under its hooks", async () => {
  assert.equal(config.require_signatures, true);
  assert.equal(config.chain, true);
  assert.equal(config.mode, "trial");
  const cfg = await (await fetch(`${base}/api/config`)).json();
  assert.ok(cfg.profiles.includes("security-signed/1.0") && cfg.profiles.includes("modes/1.0"));
  assert.equal(cfg.mcp, true);
  const hooksPath = (await git(repo, ["config", "--get", "core.hooksPath"])).trim();
  assert.equal(hooksPath, join(projectDir, "hooks"));
  assert.match(await readFile(join(repo, "lib/calc.mjs"), "utf8"), /export function add/);
});

test("a change in the working tree is proposed as a patch that waits in the desk", async () => {
  await writeFile(join(repo, "lib/calc.mjs"), (await readFile(join(repo, "lib/calc.mjs"), "utf8")) + "\nexport function square(a) {\n  return a * a;\n}\n");
  const client = await agentClient(gate);
  const artefact = await describeChange(repo, { summary: "Add square", drafted_by: "the test" });
  assert.equal(artefact.branch, "main");
  assert.equal(artefact.base, await head(repo));
  assert.deepEqual(artefact.files, [{ path: "lib/calc.mjs", added: 4, removed: 0 }]);
  assert.match(artefact.patch, /^diff --git a\/lib\/calc\.mjs b\/lib\/calc\.mjs/);
  const first = await propose(client, artefact, { gate });
  assert.equal(first.state, "review_requested");
  assert.equal(first.revised, false);
  // The same change again is the same task, and the trial mode would have required the review anyway.
  const again = await propose(client, artefact, { gate });
  assert.equal(again.task_id, first.task_id);
  const reviews = await openReviews();
  const review = reviews.find((r) => r.task_id === first.task_id);
  assert.equal(review.artefact.patch, artefact.patch);
  assert.equal(review.input.summary, "Add square");
  assert.equal(review.kind, "code_change");
});

test("the hooks refuse a commit with no approval, then let the approved change through with its trailers and its note", async () => {
  await assert.rejects(commitIn(repo, "Add square"), (e) => /no approved change matches/.test(e.stderr));
  assert.equal(await head(repo), (await git(repo, ["rev-parse", "main"])).trim(), "nothing was committed");
  const review = (await openReviews()).find((r) => r.input.summary === "Add square");
  assert.equal((await decide("decide.approve", review, { comment: "fine" })).state, "completed");
  await commitIn(repo, "Add square");
  const sha = await head(repo);
  const info = await commitInfo(repo, sha);
  const trailers = Object.fromEntries((await parseTrailers(repo, info.message)).map((t) => [t.token, t.value]));
  assert.equal(trailers["CHAP-Task"], review.task_id);
  assert.equal(trailers["CHAP-Decision"], "approve");
  assert.equal(trailers["CHAP-Reviewer"], human.from);
  assert.equal(trailers["CHAP-Agent"], config.agent.uri);
  assert.equal(trailers["CHAP-Artefact"], await contentHash(review.artefact));
  assert.match(trailers["CHAP-Chain-Head"], /^sha256:[0-9a-f]{64}$/);
  const note = JSON.parse(await readNote(repo, sha));
  assert.equal(note.task_id, review.task_id);
  assert.equal(note.decision_envelope.method, "decide.approve");
  assert.match(note.decision_envelope.sig, /^ed25519:/);
  assert.equal(note.decision_envelope.params.approved_artefact_digest, trailers["CHAP-Artefact"]);
  assert.equal(envelopeVerifies(note.decision_envelope, note.reviewer_keys).ok, true);
  assert.equal(note.reviewer_keys[0].kid, human.signer.kid);
  const offline = await verifyRange(repo, "HEAD");
  assert.equal(offline[0].status, "ok", offline[0].detail);
  assert.match(offline[0].detail, /signed/);
  const online = await verifyRange(repo, "HEAD", { coordinator: `${base}/chap` });
  assert.match(online[0].detail, /completed at the coordinator/);
});

test("verification fails a tampered commit, a commit without an approval, and a note for another task", async () => {
  const approved = await head(repo);
  // A commit that bypassed the gate carries no trailers.
  await writeFile(join(repo, "README.md"), (await readFile(join(repo, "README.md"), "utf8")) + "\nSlipped past.\n");
  await commitIn(repo, "Slipped past", { CHAP_GATE: "off" });
  const slipped = await head(repo);
  let results = await verifyRange(repo, `${approved}..HEAD`);
  assert.equal(results.length, 1);
  assert.equal(results[0].status, "FAIL");
  assert.match(results[0].detail, /no CHAP-Task trailer/);
  // The approved commit's note copied onto a commit with a different tree fails on the tree.
  await git(repo, ["notes", "--ref=refs/notes/chap", "copy", "-f", approved, slipped]);
  const message = (await commitInfo(repo, approved)).message;
  await git(repo, ["commit", "--amend", "-q", "-F", "-"], { env: { CHAP_GATE: "off" }, input: `Slipped past\n\n${message.split("\n\n").slice(1).join("\n\n")}` });
  const forged = await head(repo);
  await git(repo, ["notes", "--ref=refs/notes/chap", "copy", "-f", approved, forged]);
  results = await verifyRange(repo, `${approved}..HEAD`);
  assert.equal(results[0].status, "FAIL");
  assert.match(results[0].detail, /does not (give this commit's tree|apply to the parent)/);
  // A tampered envelope does not verify.
  const note = JSON.parse(await readNote(repo, approved));
  const tampered = { ...note.decision_envelope, params: { ...note.decision_envelope.params, comment: "changed" } };
  assert.equal(envelopeVerifies(tampered, note.reviewer_keys).ok, false);
  await git(repo, ["reset", "-q", "--hard", approved]);
});

test("an override is applied to the working tree before the commit, and the commit verifies as approved with an edit", async () => {
  const client = await agentClient(gate);
  await writeFile(join(repo, "lib/calc.mjs"), (await readFile(join(repo, "lib/calc.mjs"), "utf8")) + "\nexport function negate(a) {\n  return -a;\n}\n");
  const artefact = await describeChange(repo, { summary: "Add negate", drafted_by: "the test" });
  const { task_id } = await propose(client, artefact, { gate });
  const review = (await openReviews()).find((r) => r.task_id === task_id);
  const edited = { ...review.artefact, patch: review.artefact.patch.replace("+  return -a;", "+  return 0 - a;") };
  const diff = jsonPatch(review.artefact, edited);
  assert.deepEqual(diff.map((op) => op.path), ["/patch"]);
  const decided = await decide("decide.override", review, { rationale: "spell it out", diff, intent_preserved: true });
  assert.equal(decided.state, "completed");
  assert.equal(decided.applied.patch, edited.patch);
  assert.ok(await syncOverride(repo, review.artefact, decided.applied));
  assert.match(await readFile(join(repo, "lib/calc.mjs"), "utf8"), /return 0 - a;/);
  await commitIn(repo, "Add negate");
  const results = await verifyRange(repo, "HEAD");
  assert.equal(results[0].status, "ok", results[0].detail);
  assert.match(results[0].detail, /approved with an edit/);
  const note = JSON.parse(await readNote(repo, await head(repo)));
  assert.equal(note.proposed_artefact.patch, review.artefact.patch);
  assert.equal(note.approved_artefact.patch, edited.patch);
  assert.deepEqual(applyJsonPatch(note.proposed_artefact, note.decision_envelope.params.diff), note.approved_artefact);
});

test("the built-in agent takes tasks.csv through the gate: an approval, a revision then an override, and a rejection", async () => {
  const branch = "agent/test";
  const lines = [];
  const running = runAgent({ source: join(projectDir, "tasks.csv"), repo, branch, once: true, pollMs: 50, log: (l) => lines.push(l), gateDir: projectDir, keyPath });
  const first = await until(async () => (await openReviews()).find((r) => r.input.summary === "Validate the inputs of add"));
  assert.deepEqual(first.artefact.files.map((f) => f.path), ["lib/calc.mjs", "test/calc.test.mjs"]);
  assert.match(first.artefact.patch, /assertNumber/);
  await decide("decide.approve", first, { comment: "good" });

  const second = await until(async () => (await openReviews()).find((r) => r.input.summary === "Add a subtract function"));
  await decide("decide.reject", second, { comment: "name the parameters minuend and subtrahend", request_revision: true });
  const revised = await until(async () => (await openReviews()).find((r) => r.task_id === second.task_id && r.artefact.patch.includes("Revision: name the parameters")));
  assert.equal(revised.decisions.at(-1).kind, "reject");
  const edited = { ...revised.artefact, patch: revised.artefact.patch.replace("+  return a - b;", "+  return a - b; // minuend less subtrahend") };
  await decide("decide.override", revised, { rationale: "say which is which", diff: jsonPatch(revised.artefact, edited), intent_preserved: true });

  const third = await until(async () => (await openReviews()).find((r) => r.input.summary === "Document the library"));
  await decide("decide.reject", third, { comment: "not this week" });

  const outcomes = await running;
  assert.deepEqual(Object.values(outcomes), ["approve", "override", "reject"]);
  assert.ok(lines.some((l) => l.includes("revised with scripted")));
  assert.equal((await git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])).trim(), branch);
  const results = await verifyRange(repo, `main..${branch}`);
  assert.deepEqual(results.map((r) => r.status), ["ok", "ok"]);
  assert.match(results[1].detail, /approved with an edit/);
  assert.match(await git(repo, ["show", `${branch}:lib/calc.mjs`]), /minuend less subtrahend/);
  assert.doesNotMatch(await git(repo, ["show", `${branch}:README.md`]), /## Use/, "the rejected change was not committed");
  assert.equal((await git(repo, ["status", "--porcelain"])).trim(), "", "the rejected change was taken out of the working tree");
  // The repository's own tests pass with the committed changes. The child
  // must not look like one of this runner's workers, so the test context
  // is dropped from its environment.
  const env = { ...process.env };
  delete env.NODE_TEST_CONTEXT;
  delete env.NODE_OPTIONS;
  const { stdout } = await run("node", ["--test", "--test-reporter=tap"], { cwd: repo, env });
  assert.match(stdout, /# pass 4/);
});

test("a restarted agent commits nothing twice and opens no new task", async () => {
  const before = (await (await fetch(`${base}/api/tasks`)).json()).tasks.length;
  const lines = [];
  const outcomes = await runAgent({ source: join(projectDir, "tasks.csv"), repo, branch: "agent/test", once: true, pollMs: 50, log: (l) => lines.push(l), gateDir: projectDir, keyPath });
  assert.deepEqual(Object.values(outcomes), ["approve", "override", "reject"]);
  assert.equal(lines.filter((l) => l.includes("committed earlier")).length, 2);
  assert.equal((await (await fetch(`${base}/api/tasks`)).json()).tasks.length, before);
  const results = await verifyRange(repo, "main..agent/test");
  assert.equal(results.length, 2);
});

test("the report counts what the gate recorded and lists the reviewers' words", async () => {
  const tasks = (await (await fetch(`${base}/api/tasks?kind=code_change`)).json()).tasks;
  const s = summarise(tasks);
  assert.equal(s.total, 5);
  assert.equal(s.approved, 2);
  assert.equal(s.overridden, 2);
  assert.equal(s.rejected, 1);
  assert.equal(s.revisions, 1);
  assert.ok(s.override_rationales.some((o) => o.rationale === "say which is which"));
  assert.ok(s.rejection_notes.some((r) => r.note === "name the parameters minuend and subtrahend" && r.revision));
  assert.ok(s.by_model.some(([model]) => model === "scripted"));
});

test("the evidence endpoint holds the submissions, the decisions and the reviewer's keys", async () => {
  const tasks = (await (await fetch(`${base}/api/tasks?kind=code_change&state=completed`)).json()).tasks;
  const ev = await (await fetch(`${base}/api/tasks/${tasks[0].task_id}/evidence`)).json();
  assert.ok(ev.submissions.length >= 1);
  assert.ok(ev.decisions.length >= 1);
  assert.equal(ev.decisions.at(-1).envelope.params.from, human.from);
  assert.deepEqual(Object.keys(ev.keys), [human.from]);
  assert.match(ev.chain_head, /^sha256:/);
  assert.equal((await fetch(`${base}/api/tasks/nope/evidence`)).status, 404);
});

test("the pieces: the CSV, a model's answer, the prompt, and RFC 6902", async () => {
  assert.deepEqual(parseCsv('title,brief\n"A, b","say ""hi"""\n'), [{ title: "A, b", brief: 'say "hi"' }]);
  assert.deepEqual(parseAnswer('```json\n{"summary":"s","files":[{"path":"a.txt","content":"x"}],"delete":["b.txt"]}\n```').delete, ["b.txt"]);
  assert.throws(() => parseAnswer('{"files":[{"path":"../x","content":""}]}'), /outside/);
  assert.throws(() => parseAnswer("no json here"), /no JSON/);
  const context = await contextFiles(repo, { title: "Document the library", brief: "README.md should say", files: ["README.md"] });
  assert.equal(context.shown[0].path, "README.md");
  const answer = parseAnswer(scriptedDraft(buildPrompt({ title: "Document the library", brief: "the README", files: [] }, context)));
  assert.equal(answer.files[0].path, "README.md");
  assert.match(answer.files[0].content, /## Use/);
  assert.deepEqual(applyJsonPatch({ a: 1, b: [1, 2] }, [{ op: "replace", path: "/a", value: 2 }, { op: "add", path: "/b/-", value: 3 }, { op: "remove", path: "/b/0" }]), { a: 2, b: [2, 3] });
  assert.throws(() => applyJsonPatch({}, [{ op: "move", from: "/a", path: "/b" }]), /Unsupported/);
});
