// Shared by the tests: a gate in-process on an in-memory store, an agent key,
// reviewers who sign in the test as the desk would, and a demo repository.
// The decisions here are scripted because these are tests; in the project
// the decision is made in the desk.
import { execFile } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { loadConfig, makeCoordinator, makeServer } from "../server.mjs";
import { generateSigner } from "../desk/chap-client.mjs";
import { generateKeyFile } from "../keys.mjs";
import { createDemoRepo } from "../demo-repo.mjs";
import { agentClient, contentHash, reviewerClient } from "../lib/gate.mjs";

export const run = promisify(execFile);
export const projectDir = fileURLToPath(new URL("..", import.meta.url));
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function until(read, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const value = await read();
    if (value) return value;
    if (Date.now() > deadline) throw new Error("timed out waiting");
    await sleep(50);
  }
}

/**
 * A gate on a free port. `humans` are the reviewer URIs chap.config.json
 * names; `review` is its review rule. Returns the server, the gate handle
 * the libraries take, an agent client with a key on disk, and `reviewer(uri)`
 * to make a signing reviewer who has joined.
 */
export async function startGate({ suffix = "", humans = null, review = null } = {}) {
  const config = await loadConfig();
  config.store = ":memory:";
  if (suffix) config.workspace = `${config.workspace}_${suffix}`;
  if (humans) config.humans = humans.map((uri) => ({ uri, display_name: uri, role: "reviewer" }));
  if (review) config.review = review;
  const coord = await makeCoordinator(config);
  const server = await makeServer(config, coord);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  const gate = { dir: projectDir, config, url: `${base}/chap`, base };
  const keyDir = await mkdtemp(join(tmpdir(), "chap-keys-"));
  const { path: keyPath } = await generateKeyFile(config.agent.uri, keyDir);
  const agent = await agentClient(gate, { keyPath });
  const reviewer = async (uri) => {
    const c = reviewerClient(gate, uri, await generateSigner(uri));
    await c.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [c.signer.publicJwk] } });
    return c;
  };
  const close = () => new Promise((r) => server.close(r));
  // The hooks run in their own process and read the configuration from
  // CHAP_CONFIG, so each gate here writes its own.
  const configPath = join(keyDir, "chap.config.json");
  await writeFile(configPath, JSON.stringify(config, null, 2));
  gate.configPath = configPath;
  return { config, coord, server, base, gate, keyPath, configPath, agent, reviewer, close };
}

/** The hooks read the gate from the environment; point them at this one. */
export function hookEnv(g) {
  return { ...process.env, CHAP_URL: g.gate.url, CHAP_AGENT_KEY: g.keyPath, CHAP_CONFIG: g.configPath };
}

export async function demoRepo({ hooks = true } = {}) {
  return createDemoRepo(await mkdtemp(join(tmpdir(), "chap-repo-")), { log: () => {}, hooks });
}

export async function appendTo(repo, path, text) {
  await writeFile(join(repo, path), (await readFile(join(repo, path), "utf8").catch(() => "")) + text);
}

/** Open reviews for a reviewer, from the read API. */
export async function reviewsFor(g, who) {
  return (await (await fetch(`${g.base}/api/reviews?reviewer=${encodeURIComponent(who.from)}`)).json()).reviews;
}

/** A decision as the desk sends it, with the digest of the artefact shown. */
export async function decide(who, method, review, extra = {}) {
  return who.call(method, { task_id: review.task_id, approved_artefact_digest: await contentHash(review.artefact), ...extra });
}

/** git commit -am in a repository, through its hooks, with the gate in the environment. */
export function commit(g, repo, message, env = {}) {
  return run("git", ["commit", "-q", "-am", message], { cwd: repo, env: { ...hookEnv(g), ...env } });
}

export async function git_(repo, args, input) {
  const p = run("git", args, { cwd: repo });
  if (input !== undefined) p.child.stdin.end(input);
  return (await p).stdout;
}
