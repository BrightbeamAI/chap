// The handler in a server of the application's own: Node's http with a
// prefix, Express mounting it at a path, and Fastify handing it the raw
// request. The application keeps its own routes, and the desk works under
// the prefix, since its paths are relative.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import express from "express";
import Fastify from "fastify";
import { chapHandler, createChapServer } from "../index.mjs";
import { openDesk } from "./desk-harness.mjs";
import { HUMAN, project } from "./helpers.mjs";

const call = (url) => fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "workspace.describe", params: { workspace: "wsp_test", from: HUMAN } }) });

/** What the application and the mounted desk answer at `base`, with the desk under `/review`. */
async function checkMounted(base) {
  const page = await fetch(`${base}/review/`);
  assert.equal(page.status, 200);
  assert.match(await page.text(), /<script type="module" src="desk.js">/);
  assert.match(page.headers.get("content-security-policy"), /frame-ancestors 'none'/);
  assert.equal((await fetch(`${base}/review/desk.js`)).status, 200);
  assert.equal((await (await fetch(`${base}/review/api/config`)).json()).workspace, "wsp_test");
  assert.equal((await (await call(`${base}/review/chap`)).json()).result.id, "wsp_test");
  const bare = await fetch(`${base}/review`, { redirect: "manual" });
  assert.equal(bare.status, 308, "the mount point without its slash is sent to the page");
  assert.equal(new URL(bare.headers.get("location"), base).pathname, "/review/");
  const own = await fetch(`${base}/api/config`);
  assert.equal(await own.text(), "the application's own /api/config");
  assert.equal(own.headers.get("content-security-policy"), null, "the application's answers carry nothing of the desk's");
}

test("Node's http: everything under basePath, the rest to the application", async () => {
  const { configPath } = await project();
  const chap = await chapHandler({ configPath, basePath: "/review" });
  const server = createServer((req, res) => chap(req, res, () => res.end(`the application's own ${req.url}`)));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  try { await checkMounted(`http://127.0.0.1:${server.address().port}`); } finally { await new Promise((r) => server.close(r)); }
});

test("Express: mounted at a path with app.use", async () => {
  const { configPath } = await project();
  const app = express();
  app.use("/review", await chapHandler({ configPath }));
  app.get("/api/config", (req, res) => res.send("the application's own /api/config"));
  const server = app.listen(0, "127.0.0.1");
  await new Promise((r) => server.once("listening", r));
  try { await checkMounted(`http://127.0.0.1:${server.address().port}`); } finally { await new Promise((r) => server.close(r)); }
});

test("Fastify: the raw request and response, under basePath", async () => {
  const { configPath } = await project();
  const chap = await chapHandler({ configPath, basePath: "/review" });
  const app = Fastify();
  app.get("/api/config", async () => "the application's own /api/config");
  // The handler reads the body itself, so Fastify leaves JSON bodies alone on these routes.
  app.register(async (scope) => {
    scope.removeAllContentTypeParsers();
    scope.addContentTypeParser("*", (req, payload, done) => done(null));
    const desk = (req, reply) => { reply.hijack(); return chap(req.raw, reply.raw); };
    scope.all("/review", desk);
    scope.all("/review/*", desk);
  });
  await app.listen({ port: 0, host: "127.0.0.1" });
  try { await checkMounted(`http://127.0.0.1:${app.server.address().port}`); } finally { await app.close(); }
});

test("the desk runs under a prefix: it reads its config, joins and lists the queue there", async () => {
  const { configPath } = await project();
  const chap = await createChapServer({ configPath, basePath: "/review" });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  const desk = await openDesk(`${base}/review`);
  try {
    assert.ok(await desk.until(() => desk.text("workspace") === "wsp_test"));
    assert.ok(await desk.until(() => /live/.test(desk.text("connection") ?? "")));
    assert.deepEqual(desk.errors, []);
  } finally {
    desk.close();
    await chap.close();
  }
});
