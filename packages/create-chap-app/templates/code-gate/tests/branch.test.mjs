// Branch review: an agent commits on its own, its branch is proposed as one
// review, approved, sealed with the reviewers' names and the model in each
// commit, and pushed through the pre-push hook; the verifier holds every
// sealed commit to what the reviewers saw.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildNote, checkApproval, describeChange, evidence, fingerprint, onlinePolicy, propose } from "../lib/gate.mjs";
import { commitInfo, git, parseTrailers, rawCommit, readNote, writeNote } from "../lib/git.mjs";
import { describeRange, proposeRange, sealRange, sealedMessage } from "../lib/range.mjs";
import { buildTrust } from "../trust.mjs";
import { verifyRange } from "../verify.mjs";
import { appendTo, commit, decide, demoRepo, hookEnv, projectDir, reviewsFor, run, startGate } from "./helpers.mjs";

let g, you, repo, remote, base;
const MODEL = "Claude Opus 5.5";

/** Run a command in the repository with the gate in the environment; resolves with its output, or rejects with it. */
const sh = (cmd, args, env = {}) => run(cmd, args, { cwd: repo, env: { ...hookEnv(g), ...env } });
const trailersOf = async (sha) => parseTrailers(repo, (await commitInfo(repo, sha)).message);
const values = (list, token) => list.filter((t) => t.token === token).map((t) => t.value);

/** Two commits the agent makes on its own branch, through the hooks. */
async function agentCommits(branch) {
  await git(repo, ["checkout", "-q", "-b", branch, base]);
  await appendTo(repo, "lib/calc.mjs", "\nexport function subtract(a, b) {\n  return a - b;\n}\n");
  await sh("git", ["commit", "-q", "-am", "Add subtract"]);
  await appendTo(repo, "README.md", "\n`subtract(a, b)` takes b from a.\n");
  await sh("git", ["commit", "-q", "-am", "Document subtract\n\nThe README lists every function."]);
  return (await git(repo, ["rev-parse", "HEAD"])).trim();
}

before(async () => {
  g = await startGate({ suffix: "branch", extra: { review_at: "push" } });
  you = await g.reviewer(g.config.humans[0].uri);
  repo = await demoRepo();
  remote = join(await mkdtemp(join(tmpdir(), "chap-remote-")), "remote.git");
  await git(repo, ["init", "-q", "--bare", remote]);
  await git(repo, ["remote", "add", "origin", remote]);
  // The history from before the gate goes up once around the hook.
  await sh("git", ["push", "-q", "origin", "main"], { CHAP_GATE: "off" });
  base = (await git(repo, ["rev-parse", "main"])).trim();
});

after(async () => { await g.close(); });

test("under review_at push an agent commits on its own, and its commits are refused at push", async () => {
  const head = await agentCommits("agent/refused");
  const info = await commitInfo(repo, head);
  assert.equal((await trailersOf(head)).length, 0, "an unapproved commit carries no trailers");
  assert.equal(info.message, "Document subtract\n\nThe README lists every function.");
  await assert.rejects(sh("git", ["push", "-q", "origin", "agent/refused"]), (e) => /2 commits this push sends to origin have no approval that holds/.test(e.stderr) && /propose-branch\.mjs/.test(e.stderr));
  await git(repo, ["checkout", "-q", "main"]);
});

test("a branch proposed, read commit by commit, approved, sealed with the reviewer and the model, and pushed", async () => {
  const head = await agentCommits("agent/work");
  const artefact = await describeRange(repo, { base: "origin/main", head: "agent/work", drafted_by: "the test", model: MODEL, branch: "agent/work" });
  assert.equal(artefact.base, base);
  assert.equal(artefact.head, head);
  assert.deepEqual(artefact.commits.map((c) => c.message.split("\n")[0]), ["Add subtract", "Document subtract"]);
  assert.deepEqual(artefact.commits[0].files.map((f) => f.path), ["lib/calc.mjs"]);
  assert.match(artefact.commits[1].patch, /^\+`subtract\(a, b\)` takes b from a\.$/m);
  const { task_id, state } = await proposeRange(g.agent, artefact, { gate: g.gate });
  assert.equal(state, "review_requested");

  // The lists the desk polls carry no patches; the task's own view does.
  const brief = (await (await fetch(`${g.base}/api/tasks?kind=commit_range&brief=1`)).json()).tasks.find((t) => t.task_id === task_id);
  assert.equal(brief.brief, true);
  assert.ok(brief.artefact.commits.every((c) => c.patch === undefined && c.files.length));
  assert.equal(brief.submission.envelope, undefined);
  const full = await (await fetch(`${g.base}/api/tasks/${task_id}`)).json();
  assert.equal(full.artefact.commits[1].patch, artefact.commits[1].patch);

  const review = (await reviewsFor(g, you)).find((r) => r.task_id === task_id);
  assert.equal((await decide(you, "decide.approve", review, { comment: "both read" })).state, "completed");

  // The command finds the approved review for the same commits, seals the
  // branch and pushes it through the pre-push hook.
  const { stdout } = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/work", "--by", "the test", "--model", MODEL, "--wait", "--push", "origin"]);
  assert.match(stdout, /Sealed 2 commits/);
  assert.match(stdout, /Pushed to origin agent\/work, with the evidence notes/);
  const sealed = (await git(repo, ["rev-list", "--reverse", `${base}..agent/work`])).split("\n").filter(Boolean);
  assert.equal(sealed.length, 2);
  assert.equal((await git(remote, ["rev-parse", "agent/work"])).trim(), sealed[1]);
  assert.notEqual(sealed[1], head, "the sealed commits are new commits");
  const me = g.config.humans[0];
  for (const [i, sha] of sealed.entries()) {
    const raw = await rawCommit(repo, sha);
    const original = artefact.commits[i];
    assert.equal(raw.tree, original.tree);
    assert.deepEqual(raw.author, original.author);
    assert.ok(raw.signed, "signed with the agent's key");
    assert.ok(raw.message.startsWith(`${original.message}\n\n`));
    const t = await trailersOf(sha);
    assert.deepEqual(values(t, "Reviewed-by"), [`${me.display_name} <${me.email}>`]);
    assert.deepEqual(values(t, "CHAP-Model"), [MODEL]);
    assert.deepEqual(values(t, "CHAP-Series"), [`${i + 1}/2`]);
    assert.deepEqual(values(t, "CHAP-Proposed-Commit"), [original.sha]);
    assert.deepEqual(values(t, "CHAP-Reviewer"), [`${me.uri} ${fingerprint(you.signer.publicJwk)}`]);
    assert.ok(await git(remote, ["notes", "--ref=chap", "show", sha]), "the note went up with the commits");
  }
  const note = JSON.parse(await readNote(repo, sealed[0]));
  assert.equal(note.kind, "commit_range");
  assert.equal(note.approved_artefact, null, "the branch's artefact is kept once, in the submission");

  // The verifier, against the gate and offline against a trust policy.
  const online = await verifyRange(repo, `${base}..agent/work`, { gate: g.gate, base });
  assert.deepEqual(online.map((r) => r.status), ["ok", "ok"], online.map((r) => r.detail).join("\n"));
  assert.match(online[1].detail, /commit 2 of 2 of the branch/);
  assert.match(online[1].detail, /written by Claude Opus 5\.5/);
  const { trust } = await buildTrust(g.gate, { keyPath: g.keyPath });
  assert.equal(trust.reviewers[me.uri].email, me.email);
  const trustFile = join(await mkdtemp(join(tmpdir(), "chap-trust-")), "chap-trust.json");
  await writeFile(trustFile, JSON.stringify(trust));
  const offline = await verifyRange(repo, `${base}..agent/work`, { trust: trustFile, base, requireSignedCommit: true });
  assert.deepEqual(offline.map((r) => r.status), ["ok", "ok"], offline.map((r) => r.detail).join("\n"));
  await git(repo, ["checkout", "-q", "main"]);
});

test("a sealed branch altered in any way fails", async () => {
  const sealed = (await git(repo, ["rev-list", "--reverse", `${base}..agent/work`])).split("\n").filter(Boolean);
  const [one, two] = await Promise.all(sealed.map((s) => rawCommit(repo, s)));
  const note = await readNote(repo, two.sha);
  const forge = async ({ tree = two.tree, parent = one.sha, message = two.message }) => {
    const sha = (await git(repo, ["commit-tree", tree, "-p", parent, "-F", "-"], { input: message, env: { GIT_AUTHOR_NAME: two.author.name, GIT_AUTHOR_EMAIL: two.author.email, GIT_AUTHOR_DATE: two.author.date } })).trim();
    await writeNote(repo, sha, note);
    return sha;
  };
  const check = async (sha, pattern) => {
    const [r] = await verifyRange(repo, sha, { gate: g.gate });
    assert.equal(r.status, "FAIL", `expected a failure matching ${pattern}`);
    assert.match(r.detail, pattern);
  };
  // The second commit's message, tree and evidence on the base itself.
  await check(await forge({ parent: base }), /does not sit on commit 1 of the same approved branch/);
  await check(await forge({ tree: one.tree }), /its tree is not the tree of/);
  await check(await forge({ message: two.message.replace("Document subtract", "Document subtract and more") }), /its message is not the message of/);
  await check(await forge({ message: `${two.message.trimEnd()}\nReviewed-by: Somebody Else <else@example.org>\n` }), /the trailers differ from the evidence; not borne out "Reviewed-by: Somebody Else <else@example\.org>"/);
  await check(await forge({ message: two.message.replace(`CHAP-Model: ${MODEL}`, "CHAP-Model: Another Model") }), /missing "CHAP-Model: Claude Opus 5\.5"/);
  await check(await forge({ message: two.message.replace(/^CHAP-Proposed-Commit: .*$/m, `CHAP-Proposed-Commit: ${one.sha}`) }), /it says it was proposed as/);
  // A line slipped in among the trailers: git no longer reads them as trailers at all.
  const [body, block] = [two.message.slice(0, two.message.indexOf("\n\nReviewed-by:")), two.message.slice(two.message.indexOf("\n\nReviewed-by:") + 2)];
  await check(await forge({ message: `${body}\n\nThe reviewers also said yes to this line.\n${block}` }), /no CHAP approval|lines other than the gate's trailers follow/);
  // A paragraph slipped in between the proposed message and the trailers.
  await check(await forge({ message: `${body}\n\nThe reviewers also said yes to this paragraph.\n\n${block}` }), /lines other than the gate's trailers follow|its message is not the message of/);
  // The same place in the branch used twice in one range.
  const twice = await forge({});
  const results = await verifyRange(repo, `${base}..${twice}`, { gate: g.gate });
  assert.equal(results.at(-1).status, "ok", "the copy itself is the approved commit");
  const dup = (await git(repo, ["commit-tree", two.tree, "-p", twice, "-F", "-"], { input: two.message })).trim();
  await writeNote(repo, dup, note);
  const withDup = await verifyRange(repo, `${base}..${dup}`, { gate: g.gate });
  assert.equal(withDup.at(-1).status, "FAIL");
  assert.match(withDup.at(-1).detail, /at 2\/2 was used already by/);
});

test("a commit added after the sealed branch is refused at push, and the sealed ones are not", async () => {
  await git(repo, ["checkout", "-q", "agent/work"]);
  await appendTo(repo, "README.md", "\nOne more line nobody reviewed.\n");
  await sh("git", ["commit", "-q", "-am", "Unreviewed"]);
  await assert.rejects(sh("git", ["push", "-q", "origin", "agent/work"]), (e) => /1 of the 1 commit this push sends to origin has no approval that holds/.test(e.stderr) && /Unreviewed/.test(e.stderr));
  await git(repo, ["reset", "-q", "--hard", "HEAD~1"]);
  await git(repo, ["checkout", "-q", "main"]);
});

test("changes requested on a branch: the agent amends it, and the same review carries on", async () => {
  await agentCommits("agent/revise");
  const first = await describeRange(repo, { base: "origin/main", head: "agent/revise", model: MODEL, branch: "agent/revise" });
  const p1 = await proposeRange(g.agent, first, { gate: g.gate });
  const r1 = (await reviewsFor(g, you)).find((r) => r.task_id === p1.task_id);
  assert.equal((await decide(you, "decide.reject", r1, { comment: "say what subtract returns", request_revision: true })).state, "in_progress");
  await appendTo(repo, "README.md", "It returns a number.\n");
  await sh("git", ["commit", "-q", "--amend", "-a", "--no-edit"]);
  const second = await describeRange(repo, { base: "origin/main", head: "agent/revise", model: MODEL, branch: "agent/revise" });
  const p2 = await proposeRange(g.agent, second, { gate: g.gate });
  assert.equal(p2.task_id, p1.task_id);
  assert.equal(p2.revised, true);
  assert.equal(p2.state, "review_requested");
  const r2 = (await reviewsFor(g, you)).find((r) => r.task_id === p1.task_id);
  assert.equal(r2.artefact.head, second.head);
  // An edit does not apply to a branch: the gate refuses one, whatever the coordinator accepted.
  const edited = structuredClone(r2.artefact);
  edited.summary = "edited by the reviewer";
  const { jsonPatch } = await import("../desk/chap-client.mjs");
  await decide(you, "decide.override", r2, { diff: jsonPatch(r2.artefact, edited), rationale: "an edit", intent_preserved: true });
  const ev = await evidence(g.gate, p1.task_id);
  const problems = await checkApproval(buildNote(ev, g.gate.url), onlinePolicy(g.gate, ev));
  assert.match(problems.join("; "), /approved the branch with an edit; a branch is approved as its commits stand/);
  await assert.rejects(sealRange(repo, { note: { ...buildNote(ev, g.gate.url), submission: null } }), /holds no commits/);
  await git(repo, ["checkout", "-q", "main"]);
});

test("a branch with a merge, or with a commit approved already, is not proposed", async () => {
  await agentCommits("agent/merged");
  await git(repo, ["merge", "-q", "--no-verify", "--no-ff", "-m", "Merge main", "main"]).catch(() => {});
  await git(repo, ["checkout", "-q", "-b", "side", base]);
  await writeFile(join(repo, "side.txt"), "side\n");
  await sh("git", ["add", "side.txt"]);
  await sh("git", ["commit", "-q", "-m", "Side"]);
  await git(repo, ["checkout", "-q", "agent/merged"]);
  await git(repo, ["merge", "-q", "--no-verify", "--no-ff", "-m", "Merge side", "side"]);
  await assert.rejects(describeRange(repo, { base: "origin/main", head: "agent/merged" }), /is a merge commit/);
  await git(repo, ["checkout", "-q", "main"]);
  // Sealed commits proposed again read as their messages, without the gate's lines.
  const again = await describeRange(repo, { base: base, head: "agent/work" });
  assert.ok(again.commits.every((c) => !/^(Reviewed-by|CHAP-[A-Za-z-]+):/m.test(c.message)));
  assert.equal(again.commits[1].message, "Document subtract\n\nThe README lists every function.");
});

test("a change proposed with its model, approved and committed under review_at push, names the reviewer and the model", async () => {
  await git(repo, ["checkout", "-q", "-b", "agent/single", base]);
  await appendTo(repo, "lib/calc.mjs", "\nexport const ONE = 1;\n");
  const artefact = await describeChange(repo, { summary: "Add ONE", drafted_by: "the test", model: "Claude Fable 5.1" });
  const { task_id } = await propose(g.agent, artefact, { gate: g.gate });
  const review = (await reviewsFor(g, you)).find((r) => r.task_id === task_id);
  await decide(you, "decide.approve", review);
  await commit(g, repo, "Add ONE");
  const t = await trailersOf("HEAD");
  assert.deepEqual(values(t, "CHAP-Model"), ["Claude Fable 5.1"]);
  assert.deepEqual(values(t, "Reviewed-by"), [`${g.config.humans[0].display_name} <${g.config.humans[0].email}>`]);
  const [r] = await verifyRange(repo, "HEAD", { gate: g.gate });
  assert.equal(r.status, "ok", r.detail);
  await sh("git", ["push", "-q", "origin", "agent/single"]);
  await git(repo, ["checkout", "-q", "main"]);
});

test("a trust policy names each reviewer, a name that cannot sit in a trailer is refused, and a model id reads as its name", async () => {
  const { policyFromTrust, reviewedByLine } = await import("../lib/gate.mjs");
  const { modelName } = await import("../lib/providers.mjs");
  const key = you.signer.publicJwk;
  const policy = policyFromTrust({ chap_trust: 1, rule: "any_one_approves", reviewers: { "human:ada@example.org": { name: "Ada Lovelace", email: "ada@example.org", keys: [key] }, "human:bob@example.org": [key] }, agents: {} }, "t");
  assert.equal(reviewedByLine("human:ada@example.org", policy.identities), "Ada Lovelace <ada@example.org>");
  assert.equal(reviewedByLine("human:bob@example.org", policy.identities), "human:bob@example.org");
  assert.throws(() => policyFromTrust({ chap_trust: 1, reviewers: { "human:x@y": { name: "X <x@y>", keys: [key] } }, agents: {} }, "t"), /cannot go in a commit trailer/);
  assert.throws(() => policyFromTrust({ chap_trust: 1, reviewers: { "human:x@y": { name: "X", email: "x at y", keys: [key] } }, agents: {} }, "t"), /is not an email address/);
  assert.equal(modelName("claude-opus-5-5"), "Claude Opus 5.5");
  assert.equal(modelName("claude-fable-5-1"), "Claude Fable 5.1");
  assert.equal(modelName("gpt-5.5"), "GPT-5.5");
  assert.equal(modelName("gemma3:4b"), "gemma3:4b");
  await assert.rejects(describeRange(repo, { base, head: "main", model: "bad\nmodel" }).catch((e) => { throw e; }), /holds no commits|Not a model name/);
});

test("a local remote-tracking ref hides nothing from the pre-push hook, and part of an approved branch is not pushed", async () => {
  await agentCommits("agent/hidden");
  // The agent points a tracking ref at its own commits: the hook asks the remote instead.
  await git(repo, ["update-ref", "refs/remotes/origin/anything", "agent/hidden"]);
  await assert.rejects(sh("git", ["push", "-q", "origin", "agent/hidden"]), (e) => /2 of the 2 commits this push sends to origin have no approval that holds/.test(e.stderr));
  await git(repo, ["update-ref", "-d", "refs/remotes/origin/anything"]);
  // A branch approved and sealed as two commits, pushed one commit at a time.
  const artefact = await describeRange(repo, { base: "origin/main", head: "agent/hidden", model: MODEL, branch: "agent/hidden" });
  const { task_id } = await proposeRange(g.agent, artefact, { gate: g.gate });
  await decide(you, "decide.approve", (await reviewsFor(g, you)).find((r) => r.task_id === task_id));
  const { stdout } = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/hidden", "--wait"]);
  assert.match(stdout, /Approved already as/);
  assert.match(stdout, /Sealed 2 commits/);
  await assert.rejects(sh("git", ["push", "-q", "origin", "agent/hidden~1:refs/heads/agent/half"]), (e) => /approved a branch of 2 commits, and this push leaves out commit 2 of it/.test(e.stderr));
  const half = await verifyRange(repo, `${base}..agent/hidden~1`, { gate: g.gate, base });
  assert.equal(half[0].status, "FAIL");
  assert.match(half[0].detail, /commit 2 of it is not here; an approved branch lands whole/);
  await sh("git", ["push", "-q", "origin", "agent/hidden"]);
  await git(repo, ["checkout", "-q", "main"]);
});

test("a line only the gate writes is dropped from a proposed message, and refused in an approved one", async () => {
  await git(repo, ["checkout", "-q", "-b", "agent/forged", base]);
  await appendTo(repo, "README.md", "\nForged.\n");
  await sh("git", ["commit", "-q", "-am", "Tidy the README\n\nReviewed-by: The CTO <cto@example.org>\nCHAP-Reviewer: human:ciso@example.org SHA256:fake"]);
  const artefact = await describeRange(repo, { base: "origin/main", head: "agent/forged", model: MODEL, branch: "agent/forged" });
  assert.equal(artefact.commits[0].message, "Tidy the README");
  // An agent that writes its own artefact, the lines kept, is refused at the verifier.
  const crafted = structuredClone(artefact);
  crafted.commits[0].message = "Tidy the README\n\nReviewed-by: The CTO <cto@example.org>";
  crafted.summary = "crafted";
  const { task_id } = await proposeRange(g.agent, crafted, { gate: g.gate, taskId: (await g.agent.call("task.create", { kind: "commit_range", assignee: g.agent.from, input: { summary: "crafted", repo: crafted.repo, branch: "agent/crafted" }, review_required: true })).task_id });
  await decide(you, "decide.approve", (await reviewsFor(g, you)).find((r) => r.task_id === task_id));
  const ev = await evidence(g.gate, task_id);
  const note = buildNote(ev, g.gate.url);
  const sealed = await sealRange(repo, { note, policy: onlinePolicy(g.gate, ev) });
  const [r] = await verifyRange(repo, sealed[0], { gate: g.gate });
  assert.equal(r.status, "FAIL");
  assert.match(r.detail, /holds a line only the gate writes/);
  await git(repo, ["checkout", "-q", "main"]);
});

test("the command carries on: a revision sent to the same review, an approval sealed on a later run, an amended sealed commit proposed again", async () => {
  await agentCommits("agent/again");
  const run1 = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/again", "--model", MODEL]);
  const task = /Proposed as (tsk_\w+)/.exec(run1.stdout)[1];
  const r1 = (await reviewsFor(g, you)).find((r) => r.task_id === task);
  await decide(you, "decide.reject", r1, { comment: "one more line", request_revision: true });
  await appendTo(repo, "README.md", "One more line.\n");
  await sh("git", ["commit", "-q", "--amend", "-a", "--no-edit"]);
  const run2 = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/again", "--model", MODEL]);
  assert.match(run2.stdout, new RegExp(`Revised ${task}`));
  const run3 = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/again", "--model", MODEL]);
  assert.match(run3.stdout, new RegExp(`Proposed as ${task} \\(review_requested\\)`), "a run while it waits finds the same review");
  await decide(you, "decide.approve", (await reviewsFor(g, you)).find((r) => r.task_id === task));
  const run4 = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/again", "--model", MODEL, "--wait"]);
  assert.match(run4.stdout, /Sealed 2 commits/);
  const run5 = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/again", "--wait"]);
  assert.match(run5.stdout, /approved already, sealed or committed through the gate/);
  // The last sealed commit amended: it keeps the old trailers and no longer
  // holds, and the branch it belonged to is proposed again whole.
  await appendTo(repo, "README.md", "Amended after sealing.\n");
  await sh("git", ["commit", "-q", "--amend", "-a", "--no-edit"]);
  const run6 = await sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..agent/again", "--model", MODEL]);
  assert.doesNotMatch(run6.stdout, /approved already and stay/);
  assert.match(run6.stdout, /Proposed as tsk_\w+ \(review_requested\): 2 commits on agent\/again/);
  const headNow = (await git(repo, ["rev-parse", "agent/again"])).trim();
  const fresh = (await reviewsFor(g, you)).find((r) => r.artefact.head === headNow);
  assert.ok(fresh.artefact.commits.every((c) => !/CHAP-|Reviewed-by/.test(c.message)), "the sealed commit's trailers are not proposed again");
  await git(repo, ["checkout", "-q", "main"]);
});

test("under review_at push a commit is made with the gate down, and a detached head names where to push before anything is proposed", async () => {
  await git(repo, ["checkout", "-q", "-b", "agent/offline", base]);
  await appendTo(repo, "README.md", "\nWritten with the gate down.\n");
  const { stderr } = await sh("git", ["commit", "-q", "-am", "Offline"], { CHAP_URL: "http://127.0.0.1:9/chap" });
  assert.match(stderr, /the gate is not answering, so nothing is checked now/);
  await git(repo, ["checkout", "-q", "--detach", "HEAD"]);
  await assert.rejects(sh(process.execPath, [join(projectDir, "propose-branch.mjs"), "origin/main..HEAD", "--push", "origin"]), (e) => /is not a local branch, so name where to push: --push origin --to <branch>/.test(e.stderr));
  await git(repo, ["checkout", "-q", "main"]);
});
