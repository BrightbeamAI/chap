/**
 * The dispatcher `chap-mcp-server --url` forwards each call through: what it
 * tells the client when the CHAP server gives no answer.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";

import { ChapEndpointError, remoteDispatcher } from "../src/remote.js";

const envelope = { jsonrpc: "2.0" as const, id: 1, method: "workspace.describe", params: { workspace: "w" } };

test("a server that never answers is given up on after the timeout", { timeout: 10_000 }, async () => {
  const silent = createServer(() => {});
  await new Promise<void>((r) => silent.listen(0, "127.0.0.1", r));
  const url = `http://127.0.0.1:${(silent.address() as { port: number }).port}/chap`;
  try {
    const started = Date.now();
    await assert.rejects(remoteDispatcher(url, 200).dispatch(envelope), (err: unknown) => {
      assert.ok(err instanceof ChapEndpointError);
      assert.equal(err.message, `The CHAP server at ${url} could not be reached (timed out). Start it, or check --url.`);
      return true;
    });
    assert.ok(Date.now() - started < 5_000);
  } finally {
    silent.closeAllConnections();
    silent.close();
  }
});

test("an address the client cannot use says why, where fetch alone says only that it failed", async () => {
  // Port 9 is one fetch refuses to connect to, and its error carries a reason but no code.
  await assert.rejects(remoteDispatcher("http://127.0.0.1:9/chap").dispatch(envelope),
    /could not be reached \(bad port\)/);

  const closed = createServer();
  await new Promise<void>((r) => closed.listen(0, "127.0.0.1", r));
  const url = `http://127.0.0.1:${(closed.address() as { port: number }).port}/chap`;
  await new Promise((r) => closed.close(r));
  await assert.rejects(remoteDispatcher(url).dispatch(envelope), /could not be reached \(ECONNREFUSED\)/);
});
