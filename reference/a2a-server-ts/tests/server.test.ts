/**
 * The reference server as a user starts it: the participants it refuses,
 * and the skills it offers.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";

const SERVER = fileURLToPath(new URL("../server.ts", import.meta.url));
const ENV = { PATH: process.env.PATH ?? "" };

function runOnce(args: string[], env: Record<string, string> = {}) {
  return spawnSync(process.execPath, ["--import", "tsx", SERVER, ...args],
                   { env: { ...ENV, ...env }, encoding: "utf8", timeout: 20_000 });
}

/** A port nothing listens on now. */
function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const probe = createServer().listen(0, "127.0.0.1", () => {
      const { port } = probe.address() as { port: number };
      probe.close(() => resolve(port));
    });
  });
}

/** The skill ids on the card the server serves when started with `args`. */
async function skillsServed(args: string[]): Promise<Set<string>> {
  const port = await freePort();
  const child = spawn(process.execPath, ["--import", "tsx", SERVER, "--port", String(port), ...args],
                      { env: ENV, stdio: ["ignore", "ignore", "pipe"] });
  try {
    let stderr = "";
    child.stderr!.on("data", (chunk) => { stderr += chunk; });
    for (let i = 0; i < 100 && !stderr.includes("Agent Card"); i++) await new Promise((r) => setTimeout(r, 100));
    const card = await (await fetch(`http://127.0.0.1:${port}/.well-known/agent-card.json`, { headers: { "A2A-Version": "1.0" } })).json();
    return new Set(card.skills.map((skill: { id: string }) => skill.id));
  } finally {
    child.kill();
  }
}

test("--as and CHAP_PARTICIPANT take a participant URI, and not a person's", () => {
  for (const [args, env, message] of [
    [["--as", "not a uri"], {}, /--as not a uri is not a participant URI/],
    [[], { CHAP_PARTICIPANT: "orchestrator" }, /CHAP_PARTICIPANT orchestrator is not a participant URI/],
    [["--as", "human:me@local"], {}, /--as human:me@local names a person/],
  ] as const) {
    const r = runOnce([...args], env);
    assert.equal(r.status, 2, r.stderr);
    assert.match(r.stderr, message);
  }
});

test("it offers the agent's skills, and every skill with --skills all", async () => {
  const agent = await skillsServed([]);
  assert.ok(agent.has("chap.task.create"));
  for (const skill of ["chap.workspace.create", "chap.participant.join", "chap.control.pause"]) assert.ok(!agent.has(skill), skill);

  const every = await skillsServed(["--skills", "all"]);
  for (const skill of ["chap.workspace.create", "chap.participant.join", "chap.control.pause"]) assert.ok(every.has(skill), skill);
  assert.ok(every.size > agent.size);
});
