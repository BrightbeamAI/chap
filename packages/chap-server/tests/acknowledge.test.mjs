// review.acknowledge from the desk: the first time a review is opened for
// the reviewer in a round, the desk says they have taken it up, and shows
// who has. A coordinator from before the method answers -32601, which the
// desk takes as no more to send. With a passkey, which asks for each
// signature, the desk acknowledges only a review the reviewer opened.
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { IDBFactory } from "fake-indexeddb";
import { createChapServer } from "../index.mjs";
import { makeClient } from "../desk/chap-client.mjs";
import { openDesk } from "./desk-harness.mjs";
import { softwareAuthenticator } from "./webauthn-authenticator.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

async function gate(extra = {}) {
  const { dir } = await project(extra);
  const chap = await createChapServer({ configPath: join(dir, "chap.config.json"), log: () => {} });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT });
  if (extra.require_signatures) await agent.call("participant.join", { type: "agent", role: "drafter" });
  return { chap, base, agent };
}

async function waiting(agent, summary) {
  const { task_id } = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: { summary }, review_required: true });
  await agent.call("task.complete", { task_id, output: { body: summary } });
  return task_id;
}

const acks = (desk) => desk.calls.filter((c) => c.method === "review.acknowledge");
const reviewOf = (chap, task) => chap.coord.getWorkspace("wsp_test").tasks.get(task).review;

test("the desk acknowledges a review the first time it is opened in a round, once, and shows who has", async () => {
  const { chap, base, agent } = await gate({ require_signatures: "humans" });
  const indexedDB = new IDBFactory();
  let desk = await openDesk(base, { indexedDB });
  try {
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")));
    const task = await waiting(agent, "read me");
    assert.ok(await desk.until(() => acks(desk).length === 1), "the review it brought forward is acknowledged");
    assert.equal(acks(desk)[0].reply.result.acknowledged, true);
    assert.match(acks(desk)[0].envelope.sig, /^ed25519:/, "signed, as the reviewer");
    assert.deepEqual(reviewOf(chap, task).acknowledgements.map((a) => a.reviewer), [HUMAN]);
    assert.ok(await desk.until(() => /acknowledged by Rev Iewer/.test(desk.text("r-facts"))), desk.text("r-facts"));
    assert.ok(await desk.until(() => /acknowledged by human:reviewer@example\.org/.test(desk.text("r-history"))));
    desk.close();

    desk = await openDesk(base, { indexedDB, hash: `#task=${task}` });
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.key("j");
    await desk.settle(300);
    assert.deepEqual(acks(desk), [], "on record for this round, so not again");
    desk.$("comment").value = "shorter";
    desk.$("changes").click();
    assert.ok(await desk.until(() => reviewOf(chap, task) && chap.coord.getWorkspace("wsp_test").tasks.get(task).state === "in_progress"));
    await agent.call("task.complete", { task_id: task, output: { body: "short" } });
    assert.ok(await desk.until(() => acks(desk).length === 1), "a new round is acknowledged again");
    assert.deepEqual(reviewOf(chap, task).acknowledgements.map((a) => a.reviewer), [HUMAN]);
  } finally {
    desk.close();
    await chap.close();
  }
});

test("a coordinator without review.acknowledge answers -32601, and the desk sends it no more and says nothing", async () => {
  const { chap, base, agent } = await gate();
  chap.coord.handlers.delete("review.acknowledge");
  const first = await waiting(agent, "one");
  const second = await waiting(agent, "two");
  const desk = await openDesk(base, { hash: `#task=${first}` });
  try {
    assert.ok(await desk.until(() => acks(desk).length === 1));
    assert.equal(acks(desk)[0].reply.error.code, -32601);
    desk.w.document.querySelector(`#queue .item[data-id="${second}"]`).click();
    await desk.settle(300);
    assert.equal(acks(desk).length, 1, "nothing more is sent");
    assert.equal(desk.text("notice"), "");
    assert.equal(desk.w.document.querySelector(".toast.bad"), null);
    desk.$("approve").click();
    assert.ok(await desk.until(() => chap.coord.getWorkspace("wsp_test").tasks.get(second).state === "completed"), "decisions go on as before");
  } finally {
    desk.close();
    await chap.close();
  }
});

test("with a passkey the desk acknowledges only a review the reviewer opens, so the passkey asks them only then", async () => {
  const { chap, base, agent } = await gate({ require_signatures: "humans", desk_signing: "webauthn" });
  const authenticator = softwareAuthenticator({ platform: true });
  const desk = await openDesk(base, { authenticator });
  try {
    assert.ok(await desk.until(() => !desk.$("setup-action").hidden));
    desk.$("setup-action").click();
    assert.ok(await desk.until(() => desk.$("signing").classList.contains("good")));
    const task = await waiting(agent, "read me");
    assert.ok(await desk.until(() => desk.$("decision-bar") && !desk.$("decision-bar").hidden), "the desk brought the review forward");
    await desk.settle(300);
    assert.deepEqual(acks(desk), [], "not by itself");
    const asked = authenticator.asked.length;
    desk.w.document.querySelector(`#queue .item[data-id="${task}"]`).click();
    assert.ok(await desk.until(() => acks(desk).length === 1));
    assert.match(acks(desk)[0].envelope.sig, /^webauthn:/);
    assert.equal(authenticator.asked.length, asked + 1, "the passkey asked once");
    assert.deepEqual(reviewOf(chap, task).acknowledgements.map((a) => a.reviewer), [HUMAN]);
  } finally {
    desk.close();
    await chap.close();
  }
});
