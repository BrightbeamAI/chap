// The keyboard: a single key press never decides. The a key asks first,
// and only the confirmation sends the approval.
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { createChapServer } from "../index.mjs";
import { makeClient } from "../desk/chap-client.mjs";
import { openDesk } from "./desk-harness.mjs";
import { AGENT, project } from "./helpers.mjs";

test("the a key asks before it approves, and Escape takes the question back", async () => {
  const { dir } = await project();
  const chap = await createChapServer({ configPath: join(dir, "chap.config.json") });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT });
  const { task_id } = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: { summary: "send it" }, review_required: true });
  await agent.call("task.complete", { task_id, output: { body: "send it" } });
  const desk = await openDesk(base, { hash: `#task=${task_id}` });
  const decisions = () => desk.calls.filter((c) => c.method.startsWith("decide."));
  try {
    assert.ok(await desk.until(() => !desk.$("approve").disabled));
    desk.key("a");
    await desk.settle(200);
    assert.equal(desk.$("confirm").hidden, false, "the a key opens a question");
    assert.match(desk.text("confirm-title"), /Approve as written\?/);
    assert.deepEqual(decisions(), [], "nothing is sent on the key press");
    desk.key("Escape");
    await desk.settle(100);
    assert.equal(desk.$("confirm").hidden, true);
    assert.deepEqual(decisions(), []);
    desk.key("a");
    await desk.settle(100);
    desk.$("confirm-send").click();
    assert.ok(await desk.until(() => decisions().length === 1));
    assert.equal(decisions()[0].method, "decide.approve");
    assert.equal(chap.coord.getWorkspace("wsp_test").tasks.get(task_id).state, "completed");
  } finally {
    desk.close();
    await chap.close();
  }
});
