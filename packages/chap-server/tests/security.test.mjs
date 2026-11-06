// What a browser is told about the desk: it runs its own scripts and styles
// only, no page may frame it, and the analytics pages, which another program
// writes, run in a sandbox with an origin of their own.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { createChapServer } from "../index.mjs";
import { makeClient } from "../desk/chap-client.mjs";
import { openDesk } from "./desk-harness.mjs";
import { AGENT, HUMAN, project } from "./helpers.mjs";

let chap, base, dir, agent, codeTask, letterTask;

before(async () => {
  ({ dir } = await project());
  chap = await createChapServer({ configPath: join(dir, "chap.config.json") });
  ({ base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} }));
  agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT });
  const patch = "diff --git a/a.txt b/a.txt\nindex 1111111..2222222 100644\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-one\n+two\n";
  codeTask = (await agent.call("task.create", { kind: "code_change", assignee: AGENT, input: { summary: "two" }, review_required: true })).task_id;
  await agent.call("task.complete", { task_id: codeTask, output: { summary: "two", patch, files: [{ path: "a.txt", added: 1, removed: 1 }], since: { head: "abc1234", patch } } });
  letterTask = (await agent.call("task.create", { kind: "draft", assignee: AGENT, input: { to: "a@example.org" }, review_required: true })).task_id;
  await agent.call("task.complete", { task_id: letterTask, output: { to: "a@example.org", body: "Hello", meta: { n: 1 } } });
  // A refusal on the chain, for the activity view to mark.
  await agent.call("decide.approve", { task_id: letterTask }).catch(() => {});
});

after(() => chap.close());

const POLICY = /^default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'$/;

test("every answer carries the policy, and no page may frame the desk", async () => {
  const answers = [
    await fetch(`${base}/`),
    await fetch(`${base}/desk.js`),
    await fetch(`${base}/api/config`),
    await fetch(`${base}/api/tasks/nope`),
    await fetch(`${base}/chap`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "workspace.describe", params: { workspace: "wsp_test", from: HUMAN } }) }),
  ];
  for (const r of answers) {
    assert.match(r.headers.get("content-security-policy") ?? "", POLICY, r.url);
    assert.equal(r.headers.get("x-frame-options"), "DENY", r.url);
    assert.equal(r.headers.get("x-content-type-options"), "nosniff", r.url);
    assert.equal(r.headers.get("referrer-policy"), "no-referrer", r.url);
  }
});

test("a page dropped in analytics/ runs in a sandbox, away from the desk's origin", async () => {
  await mkdir(join(dir, "analytics"), { recursive: true });
  await writeFile(join(dir, "analytics", "k.html"), "<script>fetch('/chap')</script>");
  const r = await fetch(`${base}/analytics/k.html`);
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("content-security-policy"), "sandbox allow-scripts; frame-ancestors 'none'");
  assert.equal(r.headers.get("x-frame-options"), "DENY");
  // Its scripts get an opaque origin, so a call they make carries Origin: null, which is refused.
  const call = await fetch(`${base}/chap`, { method: "POST", headers: { "content-type": "application/json", origin: "null" }, body: "{}" });
  assert.equal(call.status, 403);
});

test("the page loads only the desk's own files: no inline script or style, no handler attribute, no data: URL", async () => {
  const html = await (await fetch(`${base}/`)).text();
  for (const [, attrs, body] of html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)) {
    assert.match(attrs, /\ssrc="(?!data:|javascript:|https?:)[^"]+"/, `a script with a source of its own: ${attrs}`);
    assert.equal(body.trim(), "", "no inline script");
  }
  assert.doesNotMatch(html, /<style\b/i);
  assert.doesNotMatch(html, /\sstyle\s*=/i);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i);
  assert.doesNotMatch(html, /(href|src)\s*=\s*"(data|javascript):/i);
});

test("the desk renders every view under the policy: no style attribute or blocked markup from script", async () => {
  const desk = await openDesk(base, { hash: `#task=${codeTask}`, waitMs: 1200 });
  try {
    assert.ok(await desk.until(() => desk.$("r-since-card") && !desk.$("r-since-card").hidden), "the code change shows what changed since");
    desk.key("2");
    assert.ok(await desk.until(() => desk.w.document.querySelector("#activity .badge.bad")), "the activity marks the refusal");
    desk.key("3");
    assert.ok(await desk.until(() => desk.w.document.querySelector("#insights .legend")), "insights draws its legend");
    desk.key("1");
    desk.w.location.hash = `#task=${letterTask}`;
    desk.w.document.querySelector(`#queue .item[data-id="${letterTask}"]`)?.click();
    assert.ok(await desk.until(() => desk.text("r-title") === "a@example.org" || desk.w.document.querySelector("#r-artefact .letter")));
    desk.key("e");
    assert.ok(await desk.until(() => desk.w.document.querySelector("#r-artefact textarea.code")), "the edit form shows the JSON field");
    assert.deepEqual(desk.blocked, []);
  } finally {
    desk.close();
  }
});
