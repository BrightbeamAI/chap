// analytics.py on a store the TypeScript coordinator wrote. The workspace is
// driven in-process, its snapshot written to a SQLite file in the shape the
// coordinator's SqliteStore writes (a chap_workspaces row holding the
// snapshot as JSON), and the script run on it. Skipped where python3 has no
// chap-analytics; PYTHON names another interpreter.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile, spawnSync } from "node:child_process";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { loadConfig, makeCoordinator, makeServer } from "../server.mjs";
import { generateSigner, jsonPatch } from "../desk/chap-client.mjs";
import { generateKeyFile } from "../keys.mjs";
import { createDemoRepo } from "../demo-repo.mjs";
import { agentClient, contentHash, describeChange, propose, reviewerClient } from "../lib/gate.mjs";
import { git } from "../lib/git.mjs";

const run = promisify(execFile);
const projectDir = fileURLToPath(new URL("..", import.meta.url));
const python = process.env.PYTHON ?? "python3";
const ready = spawnSync(python, ["-c", "import chap_analytics"], { stdio: "ignore" }).status === 0;

test("analytics.py reads the gate's store and writes the report, the cases and the refinement page", { skip: ready ? false : `${python} has no chap-analytics` }, async () => {
  const config = await loadConfig();
  config.store = ":memory:";
  config.workspace = `${config.workspace}_analytics`;
  const coord = await makeCoordinator(config);
  const server = await makeServer(config, coord);
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const keyDir = await mkdtemp(join(tmpdir(), "chap-keys-"));
    const { path: keyPath } = await generateKeyFile(config.agent.uri, keyDir);
    const gate = { dir: projectDir, config, url: `${base}/chap`, base };
    const agent = await agentClient(gate, { keyPath });
    const human = reviewerClient(gate, config.humans[0].uri, await generateSigner(config.humans[0].uri));
    await human.call("participant.join", { type: "human", role: "reviewer", jwks: { keys: [human.signer.publicJwk] } });
    const repo = await createDemoRepo(await mkdtemp(join(tmpdir(), "chap-repo-")), { log: () => {}, hooks: false });
    const decide = async (method, id, extra) => {
      const view = await (await fetch(`${base}/api/tasks/${id}`)).json();
      return human.call(method, { task_id: id, approved_artefact_digest: await contentHash(view.artefact), ...extra });
    };
    // One change edited by the reviewer, one rejected.
    await writeFile(join(repo, "lib/calc.mjs"), (await readFile(join(repo, "lib/calc.mjs"), "utf8")) + "\nexport const TWO = 2;\n");
    const first = await propose(agent, await describeChange(repo, { summary: "Add TWO", drafted_by: "the test" }));
    const view = await (await fetch(`${base}/api/tasks/${first.task_id}`)).json();
    const edited = { ...view.artefact, patch: view.artefact.patch.replace("+export const TWO = 2;", "+export const TWO = 1 + 1;") };
    await decide("decide.override", first.task_id, { rationale: "spell out the sum", tags: ["clarity"], diff: jsonPatch(view.artefact, edited), intent_preserved: true });
    await git(repo, ["checkout", "--", "."]);
    await writeFile(join(repo, "README.md"), "# calc\n\nRewritten.\n");
    const second = await propose(agent, await describeChange(repo, { summary: "Rewrite the README", drafted_by: "the test" }));
    await decide("decide.reject", second.task_id, { comment: "keep the function list", tags: ["docs"] });

    const dir = await mkdtemp(join(tmpdir(), "chap-analytics-"));
    const store = join(dir, "chap.db");
    const snapshot = JSON.stringify(coord.snapshot().find((w) => w.id === config.workspace));
    const write = spawnSync(python, ["-c", [
      "import sqlite3, sys",
      "con = sqlite3.connect(sys.argv[1])",
      "con.execute('CREATE TABLE chap_workspaces (id TEXT PRIMARY KEY, version INTEGER NOT NULL, data TEXT NOT NULL, updated_at TEXT NOT NULL)')",
      "con.execute('INSERT INTO chap_workspaces VALUES (?, 1, ?, ?)', (sys.argv[2], sys.stdin.read(), '2026-01-01T00:00:00Z'))",
      "con.commit()",
    ].join("\n"), store, config.workspace], { input: snapshot });
    assert.equal(write.status, 0, String(write.stderr));
    const out = join(dir, "analytics");
    const { stdout } = await run(python, [join(projectDir, "analytics.py"), "--store", store, "--workspace", config.workspace, "--out", out]);
    assert.match(stdout, /2 tasks, 2 decisions, 1 overrides/);
    const refine = await readFile(join(out, "refine.md"), "utf8");
    assert.match(refine, /spell out the sum/);
    assert.match(refine, /## Files reviewers edited/);
    assert.match(refine, /`lib\/calc\.mjs`/);
    assert.match(refine, /keep the function list/);
    const report = await readFile(join(out, "report.html"), "utf8");
    assert.match(report, /<html/i);
    const cases = (await readFile(join(out, "cases.jsonl"), "utf8")).trim().split("\n").map((l) => JSON.parse(l));
    const corrected = cases.find((c) => c.outcome === "overridden");
    assert.match(corrected.agent_output.patch, /TWO = 2/);
    assert.match(corrected.corrected_output.patch, /TWO = 1 \+ 1/);
    const summary = JSON.parse(await readFile(join(out, "summary.json"), "utf8"));
    assert.equal(summary.workspace, config.workspace);
    // The server serves what the script wrote, and nothing outside it.
    assert.equal((await fetch(`${base}/analytics/nothing.html`)).status, 404);
    assert.equal((await fetch(`${base}/analytics/..%2Fchap.config.json`)).status, 404);
  } finally {
    await new Promise((r) => server.close(r));
  }
});
