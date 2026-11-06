// The configuration as the server reads it: a relative store path is the
// project's, whatever the working directory, ~ is the home directory, the
// port with none configured is clear of Cursor's OAuth callback, and
// require_signatures takes "humans" as well as true and false.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readdir } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { createChapServer, loadConfig, publicConfig } from "../index.mjs";
import { project } from "./helpers.mjs";

const run = promisify(execFile);
const BIN = fileURLToPath(new URL("../bin/chap-server.mjs", import.meta.url));

/** Set the environment given, undefined unsetting a variable, and put it back afterwards. */
async function withEnv(env, fn) {
  const saved = Object.fromEntries(Object.keys(env).map((k) => [k, process.env[k]]));
  const set = (vars) => { for (const [k, v] of Object.entries(vars)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; } };
  set(env);
  try { return await fn(); } finally { set(saved); }
}

test("a relative store path is taken from the project's directory, and ~ is the home directory", async () => {
  const { dir, configPath } = await project({ store: "./data/chap.db" });
  assert.equal((await loadConfig(configPath)).store, join(dir, "data", "chap.db"));
  const tilde = await project({ store: "~/chap-test.db" });
  assert.equal((await loadConfig(tilde.configPath)).store, join(homedir(), "chap-test.db"));
  assert.equal(await withEnv({ CHAP_DB_PATH: "~/elsewhere.db" }, async () => (await loadConfig(configPath)).store), join(homedir(), "elsewhere.db"));
  assert.equal(await withEnv({ CHAP_DB_PATH: "other.db" }, async () => (await loadConfig(configPath)).store), join(dir, "other.db"));
  assert.equal(await withEnv({ CHAP_DB_PATH: ":memory:" }, async () => (await loadConfig(configPath)).store), ":memory:");
});

test("a server started from another directory opens the project's store, and says where it is", async () => {
  const { dir, configPath } = await project({ store: "./data/chap.db" });
  const elsewhere = await mkdtemp(join(tmpdir(), "chap-cwd-"));
  const child = run(process.execPath, [BIN, "--config", configPath], { cwd: elsewhere, timeout: 3000 }).catch((e) => e);
  const out = await child;
  assert.match(out.stdout, new RegExp(`store    ${join(dir, "data", "chap.db").replace(/[.]/g, "\\.")}`));
  assert.ok((await readdir(join(dir, "data"))).includes("chap.db"), "the store is in the project");
  assert.deepEqual(await readdir(elsewhere), [], "nothing is written where the server was started");
});

test("with no port configured the server takes 8792, clear of Cursor's OAuth callback on 8787", async () => {
  const { configPath } = await project({ port: undefined });
  assert.equal(await withEnv({ PORT: undefined }, async () => (await loadConfig(configPath)).port), 8792);
});

test("require_signatures \"humans\" asks for signatures with the humans policy, and true for every call", async () => {
  const humans = await project({ require_signatures: "humans" });
  const config = await loadConfig(humans.configPath);
  assert.equal(config.require_signatures, true);
  assert.equal(config.signature_policy, "humans");
  const chap = await createChapServer({ config });
  assert.equal(chap.coord.options.requireSignatures, true);
  assert.equal(chap.coord.options.signaturePolicy, "humans");
  assert.deepEqual([publicConfig(config).require_signatures, publicConfig(config).signature_policy], [true, "humans"]);
  const all = await loadConfig((await project({ require_signatures: true })).configPath);
  assert.deepEqual([all.require_signatures, all.signature_policy], [true, "all"]);
  const off = await loadConfig((await project({ require_signatures: false })).configPath);
  assert.deepEqual([off.require_signatures, off.signature_policy], [false, null]);
  const fromEnv = await withEnv({ CHAP_REQUIRE_SIGNATURES: "humans" }, () => loadConfig(humans.configPath));
  assert.equal(fromEnv.signature_policy, "humans");
  await assert.rejects(loadConfig((await project({ require_signatures: "agents" })).configPath), /require_signatures .* is true, false or "humans"/);
});
