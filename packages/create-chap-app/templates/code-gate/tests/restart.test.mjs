// Stopped and started again: the demo's agent, stopped while its draft
// waited for a reviewer, carries on with that draft; a hook that finds the
// gate down says how to start this gate, from whichever repository it runs in.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { workingTreePatch } from "@brightbeamai/chap-code-gate/git";
import { run as runAgent } from "../agent.mjs";
import { appendTo, commit, decide, demoRepo, hookEnv, projectDir, reviewsFor, startGate, until } from "./helpers.mjs";

test("an agent stopped while its draft waits for a reviewer carries on with that draft when it starts again", async () => {
  const g = await startGate({ suffix: "restart" });
  const repo = await demoRepo();
  const source = join(await mkdtemp(join(tmpdir(), "chap-tasks-")), "tasks.csv");
  await writeFile(source, "title,brief,files\nValidate the inputs of add,\"Make add throw a TypeError.\",lib/calc.mjs\n");
  try {
    const child = spawn(process.execPath, [join(projectDir, "agent.mjs"), source, "--repo", repo, "--branch", "agent/r", "--poll", "50"], { env: hookEnv(g), stdio: ["ignore", "pipe", "pipe"] });
    let out = "";
    await new Promise((resolve, reject) => {
      child.stdout.on("data", (d) => { out += d; if (/waiting for reviewers to join/.test(out)) resolve(); });
      child.on("exit", (code) => reject(new Error(`the agent exited ${code} before it waited: ${out}`)));
    });
    child.kill();
    const drafted = await workingTreePatch(repo);
    assert.ok(drafted.trim(), "the draft is in the working tree");

    const human = await g.reviewer(g.config.humans[0].uri);
    const lines = [];
    const running = runAgent({ source, repo, branch: "agent/r", once: true, pollMs: 50, log: (l) => lines.push(l), keyPath: g.keyPath, gate: g.gate });
    const review = await until(async () => (await reviewsFor(g, human))[0]);
    assert.equal(review.artefact.patch, drafted, "the draft made before the stop is the one proposed");
    await decide(human, "decide.approve", review);
    assert.deepEqual(Object.values(await running), ["approve"]);
    assert.ok(lines.some((l) => /holds the draft made before the restart/.test(l)));
  } finally {
    await g.close();
  }
});

test("a hook that finds the gate down names the command that starts this gate", async () => {
  const g = await startGate({ suffix: "down" });
  const repo = await demoRepo();
  await g.close();
  await appendTo(repo, "README.md", "\nOne more line.\n");
  await assert.rejects(commit(g, repo, "one more line"), (e) => e.stderr.includes(`Start it with: node ${JSON.stringify(join(projectDir, "server.mjs"))}`) && !/npm start/.test(e.stderr));
});
