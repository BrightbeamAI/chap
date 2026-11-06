// The bin and the entry points: the commands by name, run against the gate
// project that holds chap.config.json.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { COMMANDS, findGateDir } from "../index.mjs";

const run = promisify(execFile);
const BIN = fileURLToPath(new URL("../bin/chap-code-gate.mjs", import.meta.url));

test("the bin lists its commands, and refuses one it does not have", async () => {
  const { stdout } = await run(process.execPath, [BIN, "--help"]);
  for (const name of Object.keys(COMMANDS)) assert.match(stdout, new RegExp(`^  ${name} `, "m"), name);
  await assert.rejects(run(process.execPath, [BIN, "nonsense"]), (e) => e.code === 2 && /Unknown command nonsense/.test(e.stderr));
});

test("a command finds its gate project in the nearest directory up that holds chap.config.json", async () => {
  const dir = await mkdtemp(join(tmpdir(), "chap-gate-"));
  await writeFile(join(dir, "chap.config.json"), "{}");
  await mkdir(join(dir, "a", "b"), { recursive: true });
  assert.equal(findGateDir(join(dir, "a", "b")), dir);
  const bare = await mkdtemp(join(tmpdir(), "chap-nogate-"));
  assert.equal(findGateDir(bare), bare);
});

test("stop names the gate project it looked in", async () => {
  const dir = await mkdtemp(join(tmpdir(), "chap-gate-"));
  await writeFile(join(dir, "chap.config.json"), "{}");
  const { stdout } = await run(process.execPath, [BIN, "stop", "--gate", dir]);
  assert.match(stdout, /No gate started in the background here/);
});

test("the desk a command opens or names is at localhost for a gate on this machine, where a passkey can sign", async () => {
  const { deskUrl, reviewUrl } = await import("../lib/gate.mjs");
  assert.equal(deskUrl({ base: "http://127.0.0.1:8791" }), "http://localhost:8791/");
  assert.equal(deskUrl({ base: "http://[::1]:8791" }), "http://localhost:8791/");
  assert.equal(deskUrl({ base: "https://gate.example.org" }), "https://gate.example.org/", "a gate elsewhere keeps its name");
  assert.equal(reviewUrl({ base: "http://127.0.0.1:8791" }, "tsk_01"), "http://localhost:8791/#task=tsk_01");
});
