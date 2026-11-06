// /mcp acts as the gate's agent with no signature, under
// "require_signatures": "humans", so Claude Code can read its tasks and the
// chain there. The agent must be a member for that, and also signs its
// proposals with its key: the gate makes the key at start where there is
// none, and /mcp joins the agent with it the first time it is used.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdir } from "node:fs/promises";
import { join } from "node:path";
import { loadConfig } from "@brightbeamai/chap-server";
import { makeClient, signerFromJwk } from "@brightbeamai/chap-server/client";
import { readKeyFile } from "@brightbeamai/chap-server/keys";
import { startGate } from "../index.mjs";
import { AGENT, gateProject } from "./helpers.mjs";

const META = { "io.modelcontextprotocol/protocolVersion": "2026-07-28", "io.modelcontextprotocol/clientCapabilities": {} };

async function mcpTool(base, name, args) {
  const res = await fetch(`${base}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", "mcp-protocol-version": "2026-07-28", "mcp-method": "tools/call", "mcp-name": name },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "tools/call", params: { name, arguments: { workspace: "wsp_gate", ...args }, _meta: META } }),
  });
  const reply = await res.json();
  return { isError: !!reply.result?.isError, text: reply.result?.content?.[0]?.text ?? JSON.stringify(reply) };
}

test("the agent reads over /mcp before it has proposed anything, and its signed proposals still count", async () => {
  const { dir, configPath } = await gateProject({ require_signatures: "humans", mcp: true });
  const lines = [];
  const { server, base } = await startGate(await loadConfig(configPath), { log: (l) => lines.push(l) });
  try {
    assert.ok(lines.some((l) => l.startsWith("Made the agent's key, ")), lines.join("\n"));
    assert.deepEqual(await readdir(join(dir, "keys")), ["agent_coder.jwk.json"]);
    for (const name of ["chap.workspace.describe", "chap.audit.read"]) {
      const read = await mcpTool(base, name, {});
      assert.equal(read.isError, false, `${name}: ${read.text}`);
    }
    const key = await readKeyFile(AGENT, join(dir, "keys", "agent_coder.jwk.json"));
    const coder = makeClient({ url: `${base}/chap`, workspace: "wsp_gate", from: AGENT, signer: await signerFromJwk(AGENT, key) });
    assert.deepEqual(await coder.call("participant.join", { type: "agent", role: "drafter", jwks: { keys: [coder.signer.publicJwk] } }), { joined: true, as: AGENT });
    assert.equal((await coder.call("task.create", { kind: "code_change", assignee: AGENT, input: { summary: "x" } })).state, "created");
  } finally {
    await new Promise((r) => server.close(r));
  }
});
