// Two reviewers: a change waits for both, an approval of an earlier version
// does not count for a revision, the workspace's rule holds whatever rule
// the agent asks for, an edit does not settle it, and the commit carries one
// Reviewed-by trailer per approving reviewer.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { jsonPatch } from "@brightbeamai/chap-server/client";
import { buildNote, checkApproval, contentHash, describeChange, onlinePolicy, propose, reviewersReady, reviewRule, ruleHeld } from "@brightbeamai/chap-code-gate/gate";
import { commitInfo, head, parseTrailers } from "@brightbeamai/chap-code-gate/git";
import { verifyRange } from "@brightbeamai/chap-code-gate/verify";
import { appendTo, commit, decide, demoRepo, reviewsFor, startGate } from "./helpers.mjs";

let g, alice, bob;

before(async () => {
  g = await startGate({ suffix: "quorum", humans: ["human:alice@local", "human:bob@local"], review: { rule: "quorum:2" } });
});

after(async () => { await g.close(); });

test("a change waits for two reviewers, a revision starts a new round, and the commit carries both", async () => {
  alice = await g.reviewer("human:alice@local");
  const repo = await demoRepo();
  await appendTo(repo, "lib/calc.mjs", "\nexport const ZERO = 0;\n");
  assert.deepEqual(await reviewersReady(g.agent, reviewRule(g.gate)), { ok: false, have: 1, need: 2, to: ["human:alice@local"] });
  bob = await g.reviewer("human:bob@local");
  const first = await propose(g.agent, await describeChange(repo, { summary: "Add ZERO", drafted_by: "the test" }), { gate: g.gate });
  let review = (await reviewsFor(g, alice)).find((r) => r.task_id === first.task_id);
  assert.equal(review.rule, "quorum:2");
  assert.equal((await decide(alice, "decide.approve", review)).state, "review_requested");
  assert.equal((await decide(bob, "decide.reject", review, { comment: "name it ZERO_VALUE", request_revision: true })).state, "in_progress");
  const { readFile, writeFile } = await import("node:fs/promises");
  const { join } = await import("node:path");
  await writeFile(join(repo, "lib/calc.mjs"), (await readFile(join(repo, "lib/calc.mjs"), "utf8")).replace("ZERO = 0", "ZERO_VALUE = 0"));
  const second = await propose(g.agent, await describeChange(repo, { summary: "Add ZERO", drafted_by: "the test" }), { gate: g.gate });
  assert.equal(second.task_id, first.task_id);
  review = (await reviewsFor(g, bob)).find((r) => r.task_id === first.task_id);
  assert.deepEqual(review.decisions, [], "a new round starts with no decisions");
  assert.equal((await decide(bob, "decide.approve", review)).state, "review_requested", "Alice's approval was of the first version");
  await assert.rejects(commit(g, repo, "Add ZERO_VALUE"), (e) => /no approved change matches/.test(e.stderr));
  assert.equal((await decide(alice, "decide.approve", review)).state, "completed");
  await commit(g, repo, "Add ZERO_VALUE");
  const info = await commitInfo(repo, await head(repo));
  const reviewers = (await parseTrailers(repo, info.message)).filter((t) => t.token === "Reviewed-by").map((t) => t.value).sort();
  assert.deepEqual(reviewers, ["human:alice@local", "human:bob@local"]);
  const results = await verifyRange(repo, "HEAD", { gate: g.gate });
  assert.equal(results[0].status, "ok", results[0].detail);
  assert.match(results[0].detail, /\(quorum:2\)/);
});

test("the workspace's rule holds when the agent asks for one approval", async () => {
  const repo = await demoRepo();
  await appendTo(repo, "README.md", "\nOne approval only.\n");
  const artefact = await describeChange(repo, { summary: "One approval", drafted_by: "the test" });
  const { task_id } = await propose(g.agent, artefact, { review: { rule: "any_one_approves", to: null } });
  const review = (await reviewsFor(g, alice)).find((r) => r.task_id === task_id);
  assert.equal(review.rule, "quorum:2", "the review opens under the workspace's rule");
  assert.equal((await decide(alice, "decide.approve", review)).state, "review_requested");
  await assert.rejects(commit(g, repo, "One approval"));
});

test("a proposal is refused when the workspace's rule needs fewer approvals than the configuration", async () => {
  const client = { call: async () => ({ review_rule: "any_one_approves" }) };
  await assert.rejects(ruleHeld(client, "quorum:2"), /opens reviews under any_one_approves and chap.config.json asks for quorum:2/);
  assert.equal(await ruleHeld({ call: async () => ({ review_rule: "quorum:2" }) }, "any_one_approves"), "quorum:2");
});

test("an edit under quorum:2 completes the task at the coordinator, and the gate does not accept it", async () => {
  const repo = await demoRepo();
  await appendTo(repo, "README.md", "\nMore.\n");
  const p = await propose(g.agent, await describeChange(repo, { summary: "More README", drafted_by: "the test" }), { gate: g.gate });
  const review = (await reviewsFor(g, alice)).find((r) => r.task_id === p.task_id);
  const edited = { ...review.artefact, summary: "More README, edited" };
  const r = await decide(alice, "decide.override", review, { rationale: "wording", diff: jsonPatch(review.artefact, edited), intent_preserved: true });
  assert.equal(r.state, "completed", "the coordinator settles the review on one override");
  const ev = await (await fetch(`${g.base}/api/tasks/${p.task_id}/evidence`)).json();
  assert.match((await checkApproval(buildNote(ev, g.gate.url), onlinePolicy(g.gate, ev))).join(), /under quorum:2 an edit settles the review/);
  await assert.rejects(commit(g, repo, "More README"), (e) => /approval does not hold/.test(e.stderr) || /no approved change/.test(e.stderr));
  assert.ok(await contentHash(edited));
});
