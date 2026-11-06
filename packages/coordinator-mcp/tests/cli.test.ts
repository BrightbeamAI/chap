/**
 * The chap-mcp-server command, run as an MCP client runs it.
 *
 * It read only CHAP_PROFILES and CHAP_DB_PATH and built the server with no
 * participant and no tool filter, so one client could join as a person and
 * decide in their name, and the log credited the person. It now acts as one
 * participant, an agent by default, offers the agent's tools, refuses to act
 * as a human, expands `~` in CHAP_DB_PATH, and with --url forwards every call
 * to a running CHAP server, whose people decide in its desk.
 *
 * Each test starts the command the way Claude Desktop does: a child process
 * speaking MCP on stdio.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createServer, type Server as HttpServer } from "node:http";
import { existsSync, mkdtempSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";

import { AGENT_MCP_TOOLS, TOOL_NAMES } from "../src/index.js";

const PKG = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const CLI = join(PKG, "src", "cli.ts");
const BASE_ENV = { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? tmpdir() };

/** Run the command to completion with stdin closed: for options it refuses or answers at once. */
function runOnce(args: string[], env: Record<string, string> = {}) {
  const r = spawnSync(process.execPath, ["--import", "tsx", CLI, ...args], {
    cwd: PKG, env: { ...BASE_ENV, ...env }, input: "", encoding: "utf8", timeout: 20_000,
  });
  return { status: r.status, stdout: r.stdout, stderr: r.stderr };
}

/** Start the command as an MCP server and connect a client to it. */
async function connect(args: string[], env: Record<string, string> = {}) {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ["--import", "tsx", CLI, ...args],
    cwd: PKG,
    env: { ...BASE_ENV, ...env },
    stderr: "pipe",
  });
  let stderr = "";
  transport.stderr?.on("data", (chunk) => { stderr += String(chunk); });
  const client = new Client({ name: "cli-test", version: "1.0.0" });
  await client.connect(transport);
  const call = async (name: string, args: Record<string, unknown>) => {
    const r = await client.callTool({ name, arguments: args }) as { isError?: boolean; content: Array<{ text: string }> };
    return { isError: !!r.isError, text: r.content[0]?.text ?? "" };
  };
  /** stderr and stdout are separate pipes, so the banner can arrive after the first answer. */
  const stderrMatching = async (pattern: RegExp) => {
    for (let i = 0; i < 100 && !pattern.test(stderr); i++) await new Promise((r) => setTimeout(r, 20));
    return stderr;
  };
  return { client, call, stderrMatching, close: () => client.close() };
}

// ------------------------------------------------------------------
// Who the server acts as, and what it offers
// ------------------------------------------------------------------

test("by default it acts as agent:assistant@local and offers the agent tools", async () => {
  const s = await connect([]);
  try {
    const { tools } = await s.client.listTools();
    assert.deepEqual(tools.map((t) => t.name).sort(), [...AGENT_MCP_TOOLS].sort());
    const create = tools.find((t) => t.name === "chap.task.create")!;
    assert.ok(!(create.inputSchema.required ?? []).includes("from"), "from is filled in, so not required");
    assert.match(String((create.inputSchema.properties as any).from.description), /acts as agent:assistant@local/);
    assert.match(await s.stderrMatching(/acting as/), /acting as agent:assistant@local with the agent tools/);
  } finally {
    await s.close();
  }
});

test("a tool outside the agent set is refused when called by name", async () => {
  const s = await connect([]);
  try {
    const r = await s.call("chap.participant.join", { workspace: "w", from: "human:me@local", type: "human" });
    assert.equal(r.isError, true);
    assert.match(r.text, /not offered by this server: chap\.participant\.join/);
  } finally {
    await s.close();
  }
});

test("with every tool, a call naming a person is still refused, and the log credits the agent", async () => {
  const s = await connect(["--tools", "all"]);
  try {
    assert.equal((await s.client.listTools()).tools.length, TOOL_NAMES.length);
    const made = await s.call("chap.workspace.create", { workspace: "w" });
    assert.equal(made.isError, false, made.text);
    const asHuman = await s.call("chap.participant.join", { workspace: "w", from: "human:me@local", type: "human" });
    assert.equal(asHuman.isError, true);
    assert.match(asHuman.text, /acts as agent:assistant@local; a call cannot name another from \("human:me@local"\)/);
    const joined = await s.call("chap.participant.join", { workspace: "w", type: "agent" });
    assert.equal(joined.isError, false, joined.text);
    const log = JSON.parse((await s.call("chap.audit.read", { workspace: "w" })).text);
    assert.deepEqual(log.entries.map((e: any) => e.envelope.params.from), ["agent:assistant@local", "agent:assistant@local"]);
  } finally {
    await s.close();
  }
});

test("--as and CHAP_PARTICIPANT name the participant", async () => {
  for (const [args, env, uri] of [
    [["--as", "agent:bot@example.org"], {}, "agent:bot@example.org"],
    [[], { CHAP_PARTICIPANT: "agent:from-env@example.org" }, "agent:from-env@example.org"],
    [["--as=agent:flag@example.org"], { CHAP_PARTICIPANT: "agent:from-env@example.org" }, "agent:flag@example.org"],
  ] as const) {
    const s = await connect([...args, "--tools", "all"], env);
    try {
      assert.equal((await s.call("chap.workspace.create", { workspace: "w" })).isError, false);
      const log = JSON.parse((await s.call("chap.audit.read", { workspace: "w" })).text);
      assert.equal(log.entries[0].envelope.params.from, uri);
    } finally {
      await s.close();
    }
  }
});

test("a human participant is refused before the server starts", () => {
  for (const [args, env] of [[["--as", "human:me@local"], {}], [[], { CHAP_PARTICIPANT: "human:ceo@corp.example" }]] as const) {
    const r = runOnce([...args], env);
    assert.equal(r.status, 2, r.stderr);
    assert.match(r.stderr, /names a person/);
    assert.equal(r.stdout, "");
  }
});

// ------------------------------------------------------------------
// Options
// ------------------------------------------------------------------

test("--help prints the options and the agent tools, and exits", () => {
  const r = runOnce(["--help"]);
  assert.equal(r.status, 0, r.stderr);
  for (const option of ["--as <uri>", "--tools agent|all", "--url <endpoint>", "--db <path>", "--profiles <list>"]) {
    assert.ok(r.stdout.includes(option), `--help lists ${option}`);
  }
  for (const name of AGENT_MCP_TOOLS) assert.ok(r.stdout.includes(name), `--help lists ${name}`);
});

test("an unknown option or a bad value is a usage error", () => {
  for (const [args, message] of [
    [["--bogus"], /Unknown option --bogus/],
    [["--tools", "some"], /--tools takes agent or all/],
    [["--as", "not a uri"], /is not a participant URI/],
    [["--url", "file:///etc/passwd"], /--url takes the http or https address/],
    [["--url", "http://[::1"], /--url takes the http or https address/],
    [["--ur", "http://127.0.0.1:44999/chap"], /Unknown option --ur/],
    [["--url", "http://127.0.0.1:44999/chap", "--db", "x.db"], /--db does not apply/],
    [["--profiles", ","], /names no profiles/],
  ] as const) {
    const r = runOnce([...args]);
    assert.equal(r.status, 2, `${args.join(" ")}: ${r.stderr}`);
    assert.match(r.stderr, message);
  }
});

test("without --db or --url, stderr says the state is in memory only", () => {
  const r = runOnce([]);
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stderr, /State is in memory only/);
  assert.match(r.stderr, /the agent tools cannot create one/);
});

// SQLite is an optional native dependency; CHAP_REQUIRE_SQLITE makes its
// absence a failure, as in the coordinator's own suite.
const require_ = createRequire(import.meta.url);
let sqliteAvailable = true;
try {
  const Database = require_("better-sqlite3");
  new Database(":memory:").close();
} catch {
  sqliteAvailable = false;
}
if (!sqliteAvailable && process.env.CHAP_REQUIRE_SQLITE) throw new Error("better-sqlite3 is required when CHAP_REQUIRE_SQLITE is set");

test("a leading ~ in CHAP_DB_PATH is the home directory", { skip: !sqliteAvailable }, () => {
  const home = mkdtempSync(join(tmpdir(), "chap-mcp-home-"));
  const r = runOnce([], { HOME: home, CHAP_DB_PATH: "~/chap/chap.db" });
  assert.equal(r.status, 0, r.stderr);
  assert.ok(existsSync(join(home, "chap", "chap.db")), "the store is in the home directory");
  assert.ok(!existsSync(join(PKG, "~")), "no directory named ~ in the working directory");
  assert.ok(r.stderr.includes(`Persisting to ${join(home, "chap", "chap.db")}`), r.stderr);
});

// ------------------------------------------------------------------
// --url: forwarding to a running server
// ------------------------------------------------------------------

/**
 * A desk server in miniature: a coordinator behind POST /chap, with a
 * reviewer and the assistant joined. The person's decisions are made here,
 * in process, as the desk would make them.
 */
async function deskServer(): Promise<{ coord: Coordinator; url: string; close: () => Promise<void> }> {
  const coord = new Coordinator({ deterministicIds: true, defaultProfiles: ["core/1.0", "review/1.0"] });
  const send = (method: string, params: Record<string, unknown>) =>
    coord.dispatch({ jsonrpc: "2.0", id: method, method, params: { workspace: "w", ...params } });
  send("workspace.create", { from: "human:you@local" });
  send("participant.join", { from: "human:you@local", type: "human", role: "reviewer" });
  send("participant.join", { from: "agent:assistant", type: "agent" });
  const http: HttpServer = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(coord.dispatch(JSON.parse(body))));
    });
  });
  await new Promise<void>((r) => http.listen(0, "127.0.0.1", r));
  const port = (http.address() as { port: number }).port;
  return { coord, url: `http://127.0.0.1:${port}/chap`, close: () => new Promise((r) => http.close(() => r())) };
}

test("--url forwards each call as the participant, and the person decides at the server", async () => {
  const desk = await deskServer();
  const s = await connect(["--url", desk.url, "--as", "agent:assistant"]);
  try {
    assert.ok((await s.stderrMatching(/Forwarding/)).includes(`Forwarding every call to ${desk.url}`));
    const made = await s.call("chap.task.create", {
      workspace: "w", kind: "draft", assignee: "agent:assistant", input: { ticket: 1 }, review_required: true,
    });
    assert.equal(made.isError, false, made.text);
    const taskId = JSON.parse(made.text).task_id;
    const done = await s.call("chap.task.complete", { workspace: "w", task_id: taskId, output: { body: "draft" } });
    assert.equal(JSON.parse(done.text).state, "review_requested");

    const own = await s.call("chap.decide.approve", { workspace: "w", task_id: taskId });
    assert.equal(own.isError, true);
    assert.equal(JSON.parse(own.text).chap_error, -32011);
    const asHuman = await s.call("chap.decide.approve", { workspace: "w", task_id: taskId, from: "human:you@local" });
    assert.equal(asHuman.isError, true);
    assert.match(asHuman.text, /acts as agent:assistant;/);
    assert.equal(desk.coord.getWorkspace("w")!.tasks.get(taskId)!.state, "review_requested");

    desk.coord.dispatch({ jsonrpc: "2.0", id: "desk", method: "decide.approve",
                          params: { workspace: "w", from: "human:you@local", task_id: taskId } });
    const log = JSON.parse((await s.call("chap.audit.read", { workspace: "w", filter: { task_id: taskId } })).text);
    const decisions = log.entries.filter((e: any) => e.envelope?.method === "decide.approve");
    assert.deepEqual(decisions.map((e: any) => e.envelope.params.from), ["human:you@local"]);
  } finally {
    await s.close();
    await desk.close();
  }
});

test("--url reports a server it cannot reach, or one that refuses the request", async () => {
  const unreachable = await connect(["--url", "http://127.0.0.1:9/chap"]);
  try {
    const r = await unreachable.call("chap.workspace.describe", { workspace: "w" });
    assert.equal(r.isError, true);
    assert.match(r.text, /The CHAP server at http:\/\/127\.0\.0\.1:9\/chap could not be reached/);
  } finally {
    await unreachable.close();
  }

  const refusing = createServer((_req, res) => {
    res.writeHead(421, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "unknown host; set allowed_hosts in chap.config.json" }));
  });
  await new Promise<void>((r) => refusing.listen(0, "127.0.0.1", r));
  const url = `http://127.0.0.1:${(refusing.address() as { port: number }).port}/chap`;
  const s = await connect(["--url", url]);
  try {
    const r = await s.call("chap.workspace.describe", { workspace: "w" });
    assert.equal(r.isError, true);
    assert.match(r.text, /refused the call with HTTP 421: unknown host; set allowed_hosts/);
  } finally {
    await s.close();
    await new Promise((r) => refusing.close(() => r(undefined)));
  }
});
