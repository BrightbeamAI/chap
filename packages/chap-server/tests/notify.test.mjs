// Telling people a review or a question waits (A6, #180): a line on the
// console and a webhook, each with a summary and a link to the desk that
// grants nothing, signed with HMAC-SHA256 for the webhook. A webhook that
// hangs, fails or is gone is logged and never holds up a call.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { createServer } from "node:http";
import { createChapServer, loadConfig } from "../index.mjs";
import { makeClient } from "../desk/chap-client.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

const SECRET = "s3cret-for-the-test";

/** A webhook receiver on a free port: what it got, and how it answers. */
async function receiver(answer = (req, res) => { res.writeHead(204); res.end(); }) {
  const got = [];
  const server = createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => { got.push({ headers: req.headers, body: Buffer.concat(chunks).toString("utf8") }); answer(req, res); });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  return { got, url: `http://127.0.0.1:${server.address().port}/hooks/chap?token=in-the-url`, close: () => new Promise((r) => { server.closeAllConnections(); server.close(r); }) };
}

async function gate(notify, extra = {}) {
  const { configPath } = await project({ profiles: ["core/1.0", "review/1.0", "whisper/1.0"], notify, ...extra });
  const lines = [];
  const chap = await createChapServer({ configPath, log: (l) => lines.push(l) });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: (l) => lines.push(l) });
  const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT });
  return { chap, base, agent, lines };
}

const until = async (check, ms = 4000) => { const end = Date.now() + ms; while (Date.now() < end) { if (await check()) return true; await new Promise((r) => setTimeout(r, 20)); } return false; };

test("a review opened, a review asked for and a question asked each send a summary and a link, signed, and nothing else does", async () => {
  const hook = await receiver();
  const { chap, base, agent, lines } = await gate({ console: true, webhook: { url: hook.url, secret: SECRET } });
  try {
    const { task_id } = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: { summary: "x" }, review_required: true });
    await agent.call("task.complete", { task_id, output: { body: "hello" } });
    assert.ok(await until(() => hook.got.length === 1));
    const [first] = hook.got;
    assert.equal(first.headers["content-type"], "application/json");
    assert.equal(first.headers["x-chap-signature"], `sha256=${createHmac("sha256", SECRET).update(first.body).digest("hex")}`, "the body is signed with the secret");
    assert.match(first.headers["user-agent"], /^chap-server\//);
    const notice = JSON.parse(first.body);
    assert.deepEqual(Object.keys(notice), ["event", "workspace", "task_id", "kind", "from", "to", "summary", "link", "seq", "at"]);
    assert.deepEqual([notice.event, notice.workspace, notice.task_id, notice.kind, notice.from, notice.to], ["review.requested", "wsp_test", task_id, "draft", AGENT, [HUMAN]]);
    assert.equal(notice.summary, `${AGENT} asks ${HUMAN} to review draft ${task_id}`);
    assert.equal(notice.link, `${base}/#task=${task_id}`, "the desk, at the task, and nothing that grants a thing");
    assert.ok(lines.includes(`${notice.summary}: ${notice.link}`), lines.join("\n"));
    assert.ok(lines.includes(`  notify   console, webhook ${new URL(hook.url).origin}, linking ${base}/`), "the console names the webhook by its origin, never its path");

    // Nothing for a decision, a completion with no review, or a refused call.
    const human = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN });
    await human.call("decide.approve", { task_id });
    const plain = await agent.call("task.create", { kind: "note", assignee: AGENT, input: {} });
    await agent.call("task.complete", { task_id: plain.task_id, output: {} });
    await agent.call("decide.approve", { task_id }).catch(() => {});

    const asked = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: { summary: "y" } });
    await agent.call("review.request", { task_id: asked.task_id, to: [HUMAN], artefact: { body: "look" } });
    await agent.call("whisper.ask", { task_id: asked.task_id, to: HUMAN, question: "Formal or casual?", deadline_ms: 60000, default_if_lapsed: "formal" });
    assert.ok(await until(() => hook.got.length === 3));
    const [, review, whisper] = hook.got.map((g) => JSON.parse(g.body));
    assert.deepEqual([review.event, review.task_id, review.to], ["review.requested", asked.task_id, [HUMAN]]);
    assert.deepEqual([whisper.event, whisper.task_id, whisper.to], ["whisper.asked", asked.task_id, [HUMAN]]);
    assert.match(whisper.whisper_id, /\S/);
    assert.equal(whisper.summary, `${AGENT} asks ${HUMAN} a question on draft ${asked.task_id}`);
    await new Promise((r) => setTimeout(r, 100));
    assert.equal(hook.got.length, 3, "and nothing more");
  } finally {
    await chap.close();
    await hook.close();
  }
});

test("a webhook that hangs, fails or is gone holds up no call, and each failure is logged", async () => {
  const hung = await receiver(() => { /* never answers */ });
  const gone = await receiver();
  const goneUrl = gone.url;
  await gone.close();
  try {
    for (const [label, url, answer] of [["hangs", hung.url], ["fails", null, (req, res) => { res.writeHead(500); res.end(); }], ["is gone", goneUrl]]) {
      const failing = answer ? await receiver(answer) : null;
      const { chap, agent, lines } = await gate({ webhook: { url: url ?? failing.url, secret: SECRET } });
      try {
        const { task_id } = await agent.call("task.create", { kind: "draft", assignee: AGENT, input: {}, review_required: true });
        const started = Date.now();
        assert.equal((await agent.call("task.complete", { task_id, output: { body: "x" } })).state, "review_requested");
        assert.ok(Date.now() - started < 1000, `${label}: the call is answered at once`);
        if (label === "hangs") assert.ok(await until(() => hung.got.length === 1), "the hung webhook has the notice, and the call was answered all the same");
        else assert.ok(await until(() => lines.some((l) => l.startsWith("The webhook at http://127.0.0.1:"))), `${label}: ${lines.join("\n")}`);
        if (label === "fails") assert.ok(lines.some((l) => / answered HTTP 500 to the notice of review\.requested on /.test(l)));
        if (label === "is gone") assert.ok(lines.some((l) => /could not be reached \(ECONNREFUSED\)/.test(l)), lines.join("\n"));
        assert.ok(!lines.some((l) => l.includes("in-the-url")), "a report never shows the URL's path or query");
      } finally {
        await chap.close();
        await failing?.close();
      }
    }
  } finally {
    await hung.close();
  }
});

test("a notify setting that says something else is refused when the configuration is read", async () => {
  const read = async (notify) => loadConfig((await project({ notify })).configPath);
  await assert.rejects(read({ email: "you@example.org" }), /notify in chap\.config\.json takes \{"console": true, "webhook": \{"url": \.\.\., "secret": \.\.\.\}\}/);
  await assert.rejects(read({ console: "yes" }), /notify\.console in chap\.config\.json is true or false/);
  await assert.rejects(read({ webhook: { url: "https://hooks.example.org/x" } }), /notify\.webhook in chap\.config\.json takes \{"url": \.\.\., "secret": \.\.\.\}/);
  await assert.rejects(read({ webhook: { url: "ftp://hooks.example.org/x", secret: "s" } }), /notify\.webhook\.url in chap\.config\.json is "ftp:\/\/hooks\.example\.org\/x"; it is an http or https URL/);
  await assert.rejects(read({ webhook: { url: "https://hooks.example.org/x", secret: "" } }), /notify\.webhook\.secret in chap\.config\.json is the secret each notice is signed with/);
  assert.deepEqual((await read({ console: true })).notify, { console: true, webhook: null });
});

test("the console line shows a control character an agent put in its URI as its code point", async () => {
  const { chap, base, lines } = await gate({ console: true });
  try {
    const sneaky = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: "agent:x\u001b[2Kfake" });
    await sneaky.call("participant.join", { type: "agent" });
    const { task_id } = await sneaky.call("task.create", { kind: "draft", assignee: "agent:x\u001b[2Kfake", input: {}, review_required: true });
    await sneaky.call("task.complete", { task_id, output: {} });
    assert.ok(await until(() => lines.some((l) => l.startsWith("agent:x<U+001B>[2Kfake asks"))), lines.join("\n"));
  } finally {
    await chap.close();
  }
});
