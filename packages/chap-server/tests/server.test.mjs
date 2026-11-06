// The package on its own: a project is the directory that holds its
// chap.config.json, the desk comes from the package, and the server runs
// standalone, from the bin, or as a handler in a server of the caller's.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { chapHandler, createChapServer, loadConfig } from "../index.mjs";
import { HUMAN, project } from "./helpers.mjs";

const run = promisify(execFile);
const BIN = fileURLToPath(new URL("../bin/chap-server.mjs", import.meta.url));

test("the project is the directory that holds chap.config.json", async () => {
  const { dir, configPath } = await project();
  const config = await loadConfig(configPath);
  assert.equal(config.dir, dir);
  assert.equal(config.workspace, "wsp_test");
});

test("a project's server serves the package's desk and the project's analytics pages", async () => {
  const { dir, configPath } = await project();
  await mkdir(join(dir, "analytics"));
  await writeFile(join(dir, "analytics", "summary.json"), JSON.stringify({ written: "2026-11-06T00:00:00Z" }));
  const chap = await createChapServer({ configPath });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  try {
    assert.match(await (await fetch(`${base}/`)).text(), /CHAP review desk/);
    assert.match(await (await fetch(`${base}/chap-client.mjs`)).text(), /export function makeClient/);
    const cfg = await (await fetch(`${base}/api/config`)).json();
    assert.equal(cfg.gate_dir, dir);
    assert.deepEqual(cfg.humans.map((h) => h.uri), [HUMAN]);
    assert.equal((await (await fetch(`${base}/analytics/summary.json`)).json()).written, "2026-11-06T00:00:00Z");
  } finally {
    await chap.close();
  }
});

test("the handler mounts in a Node http server of the caller's, which serves the paths it does not", async () => {
  const { configPath } = await project();
  const handler = await chapHandler({ configPath });
  const server = createServer((req, res) => handler(req, res, () => { res.writeHead(200, { "content-type": "text/plain" }); res.end("the app's own page"); }));
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const joined = await (await fetch(`${base}/chap`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "workspace.describe", params: { workspace: "wsp_test", from: HUMAN } }) })).json();
    assert.equal(joined.result.id, "wsp_test");
    assert.equal(await (await fetch(`${base}/somewhere-else`)).text(), "the app's own page");
  } finally {
    await new Promise((r) => server.close(r));
  }
});

test("the bin says what it does", async () => {
  const { stdout } = await run(process.execPath, [BIN, "--help"]);
  assert.match(stdout, /chap-server \[--config <file>\]/);
  assert.match(stdout, /chap-server keys/);
  await assert.rejects(run(process.execPath, [BIN, "--bogus"]), (e) => /Unknown option --bogus/.test(e.stderr));
});

test("a port in use is one line that says what to do", async () => {
  const busy = createServer(() => {});
  await new Promise((r) => busy.listen(0, "127.0.0.1", r));
  const port = busy.address().port;
  try {
    const { configPath } = await project({ port });
    await assert.rejects(run(process.execPath, [BIN, "--config", configPath]), (e) => {
      assert.equal(e.code, 1);
      assert.equal(e.stderr.trim().split("\n").length, 1, e.stderr);
      assert.match(e.stderr, new RegExp(`Port ${port} on 127\\.0\\.0\\.1 is in use: .* another port with PORT or "port" in chap\\.config\\.json`));
      return true;
    });
  } finally {
    await new Promise((r) => busy.close(r));
  }
});
