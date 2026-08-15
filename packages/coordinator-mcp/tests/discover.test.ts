/**
 * MCP 2026-07-28 conformance (SEP-2575, SEP-2549, SEP-2322).
 *
 * The 2026 revision drops the initialize handshake and carries version,
 * identity and capabilities on every request instead. A server must
 * implement `server/discover`, must reject a version it does not implement
 * with `UnsupportedProtocolVersionError`, and must type every result.
 *
 * These tests drive the transport directly rather than through
 * `Client.connect()`, because connecting performs an initialize handshake
 * and so exercises the handshake era rather than the stateless one. A
 * modern client sends none of that.
 *
 * The rejection ladder pinned here matches the Python `mcp` 2.x SDK rung for
 * rung, which is what keeps the two CHAP implementations symmetric.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { Coordinator } from "@brightbeamai/chap-coordinator";
import {
  makeChapMcpServer,
  MODERN_PROTOCOL_VERSIONS,
  SUPPORTED_PROTOCOL_VERSIONS,
  UNSUPPORTED_PROTOCOL_VERSION,
  META_SERVER_INFO,
  META_PROTOCOL_VERSION,
  META_CLIENT_INFO,
  META_CLIENT_CAPABILITIES,
} from "../src/index.js";

/** A well-formed 2026 envelope. `clientInfo` is optional, the rest required. */
function envelope(version: string = "2026-07-28") {
  return {
    _meta: {
      [META_PROTOCOL_VERSION]:     version,
      [META_CLIENT_INFO]:          { name: "probe", version: "1.0.0" },
      [META_CLIENT_CAPABILITIES]:  {},
    },
  };
}

/**
 * Open a raw connection and return a sender. No handshake is performed, so
 * this is what a 2026-era client actually looks like on the wire.
 */
async function wire() {
  const coord = new Coordinator({});
  const server = makeChapMcpServer(coord);
  const [ct, st] = InMemoryTransport.createLinkedPair();
  await server.connect(st);

  const seen: any[] = [];
  ct.onmessage = (m: any) => seen.push(m);
  await ct.start();

  let id = 0;
  return async function send(method: string, params?: unknown): Promise<any> {
    const before = seen.length;
    await ct.send({ jsonrpc: "2.0", id: ++id, method, ...(params !== undefined ? { params } : {}) } as any);
    for (let i = 0; i < 200 && seen.length === before; i++) {
      await new Promise((r) => setTimeout(r, 5));
    }
    return seen[before];
  };
}

test("server/discover answers without any handshake", async () => {
  const send = await wire();
  const { result } = await send("server/discover", envelope());
  assert.deepEqual(result.supportedVersions, [...SUPPORTED_PROTOCOL_VERSIONS]);
  assert.ok(result.supportedVersions.includes("2026-07-28"));
  assert.equal(result.resultType, "complete");
  assert.ok(result.capabilities.tools, "advertises the tools capability");
  assert.equal(result._meta[META_SERVER_INFO].name, "chap");
  assert.equal(typeof result.instructions, "string");
});

test("server/discover advertises both eras, but only modern versions are declarable", async () => {
  // The advertised set spans both eras because CHAP serves both. The
  // declarable set is narrower: a handshake-era revision is negotiated by
  // `initialize`, never named in a per-request envelope.
  assert.ok(SUPPORTED_PROTOCOL_VERSIONS.includes("2025-11-25" as never));
  assert.ok(!(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes("2025-11-25"));
});

test("a request declaring an unsupported version is rejected with -32022", async () => {
  const send = await wire();
  for (const version of ["1900-01-01", "2024-11-05", "2099-01-01"]) {
    const { error } = await send("tools/list", envelope(version));
    assert.equal(error.code, UNSUPPORTED_PROTOCOL_VERSION, `version ${version} must be refused`);
    assert.equal(error.message, "Unsupported protocol version");
    assert.deepEqual(error.data, {
      supported: [...MODERN_PROTOCOL_VERSIONS],
      requested: version,
    });
  }
});

test("a handshake-era version is not declarable per request", async () => {
  // Advertised by `server/discover`, yet refused here: 2025-11-25 is reached
  // through `initialize`. The error names only versions a client can retry
  // with, so it never steers a retry back into the same refusal.
  const send = await wire();
  const { error } = await send("tools/list", envelope("2025-11-25"));
  assert.equal(error.code, UNSUPPORTED_PROTOCOL_VERSION);
  assert.deepEqual(error.data.supported, [...MODERN_PROTOCOL_VERSIONS]);
});

test("a declared version without clientCapabilities is invalid params", async () => {
  const send = await wire();
  const { error } = await send("tools/list", { _meta: { [META_PROTOCOL_VERSION]: "2026-07-28" } });
  assert.equal(error.code, -32602);
  assert.match(error.message, /missing the required envelope key/);
});

test("a non-string version is invalid params, not a version error", async () => {
  const send = await wire();
  const { error } = await send("tools/list", {
    _meta: { [META_PROTOCOL_VERSION]: 20260728, [META_CLIENT_CAPABILITIES]: {} },
  });
  assert.equal(error.code, -32602);
  assert.match(error.message, /must be a string/);
});

test("a missing envelope key outranks an unsupported version", async () => {
  // Ladder order matters for cross-implementation agreement: a malformed
  // request is malformed whatever version it names.
  const send = await wire();
  const { error } = await send("tools/list", { _meta: { [META_PROTOCOL_VERSION]: "1900-01-01" } });
  assert.equal(error.code, -32602);
});

test("server/discover is not reachable from a handshake-era peer", async () => {
  // No envelope means the request belongs to the handshake era, where the
  // method does not exist. Answering anyway would tell a dual-era client
  // that a legacy connection can speak 2026.
  const send = await wire();
  const { error } = await send("server/discover", {});
  assert.equal(error.code, -32601);
  assert.equal(error.message, "Method not found");
  assert.equal(error.data, "server/discover", "matches the Python SDK's payload");
});

test("list results are cacheable and typed for a modern caller", async () => {
  const send = await wire();
  const { result } = await send("tools/list", envelope());
  assert.equal(result.cacheScope, "public");
  assert.ok(typeof result.ttlMs === "number" && result.ttlMs > 0);
  assert.equal(result.resultType, "complete");
  assert.ok(result.tools.length > 0);
});

test("tool results are tagged complete for a modern caller", async () => {
  const send = await wire();
  // Tool names are dotted, matching the CHAP method they dispatch to. A
  // misspelling here would fall to the unknown-tool branch and the tagging
  // assertions would pass without a real call ever being made, so the
  // success case asserts on the dispatch result too.
  const ok = await send("tools/call", {
    ...envelope(),
    name: "chap.workspace.create",
    arguments: { workspace: "w", profiles: ["core/1.0"] },
  });
  assert.equal(ok.result.resultType, "complete");
  assert.ok(!ok.result.isError, "a well-formed call must actually succeed");
  assert.match(ok.result.content[0].text, /"workspace"/);

  // A call the Coordinator genuinely refuses. Reusing the workspace id above
  // would also produce an error, but for an incidental reason, so the
  // assertion would survive a regression in error surfacing.
  const bad = await send("tools/call", {
    ...envelope(),
    name: "chap.workspace.describe",
    arguments: { workspace: "wsp_does_not_exist" },
  });
  assert.equal(bad.result.resultType, "complete", "error results are terminal too");
  assert.equal(bad.result.isError, true);
  assert.match(bad.result.content[0].text, /"chap_error": -32602/);

  // An unprefixed name never maps to a CHAP method, so it is refused by the
  // adapter. A `chap.`-prefixed unknown maps through and is refused by the
  // Coordinator instead; both surface as a terminal error result.
  const unknown = await send("tools/call", {
    ...envelope(),
    name: "not.a.tool",
    arguments: {},
  });
  assert.equal(unknown.result.isError, true);
  assert.match(unknown.result.content[0].text, /Unknown CHAP tool/);

  const unknownMethod = await send("tools/call", {
    ...envelope(),
    name: "chap.not.a.tool",
    arguments: {},
  });
  assert.equal(unknownMethod.result.isError, true);
  assert.match(unknownMethod.result.content[0].text, /"chap_error": -32601/);
});

test("a handshake-era caller gets its own result shape, without 2026 fields", async () => {
  // `resultType`, `ttlMs` and `cacheScope` are 2026 vocabulary. Emitting
  // them to a 2025-era caller would put fields on the wire that its own
  // revision does not define.
  const send = await wire();
  const list = await send("tools/list", {});
  assert.ok(list.result.tools.length > 0);
  assert.equal(list.result.resultType, undefined);
  assert.equal(list.result.ttlMs, undefined);
  assert.equal(list.result.cacheScope, undefined);

  const call = await send("tools/call", {
    name: "chap.workspace.create",
    arguments: { workspace: "w2", profiles: ["core/1.0"] },
  });
  assert.equal(call.result.resultType, undefined);
  assert.ok(!call.result.isError, "a well-formed call must actually succeed");
  assert.ok(Array.isArray(call.result.content));
});
