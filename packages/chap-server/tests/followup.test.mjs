// The prompt a decision makes for the agent names the command that proposes
// the work again. Every word in it that came from the agent's artefact is
// quoted, so a branch name a shell would act on stays a word.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, readdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { followUpPrompt, proposeAgainCommand } from "../desk/followup.js";

const run = promisify(execFile);
const BRANCH = "agent/x;touch${IFS}pwned";
const BASE = "0123456789;id;";
const branchTask = { task_id: "tsk_1", output: { summary: "a branch", branch: BRANCH, base: BASE, drafted_by: "claude $(id)", commits: [{ sha: "a".repeat(40), message: "one" }] } };

/** The words a POSIX shell makes of a command line: the leading node becomes printf, which prints each word on a line. */
async function wordsOf(command, cwd) {
  const { stdout } = await run("sh", ["-c", command.replace(/^node /, "printf '%s\\n' ")], { cwd });
  return stdout.split("\n").slice(0, -1);
}

test("the command to propose a branch again quotes its range and the agent's name, so a shell runs nothing from them", async () => {
  const command = proposeAgainCommand(branchTask, "/gate dir");
  const cwd = await mkdtemp(join(tmpdir(), "chap-words-"));
  assert.deepEqual(await wordsOf(command, cwd), ["/gate dir/propose-branch.mjs", `${BASE.slice(0, 12)}..${BRANCH}`, "--by", "claude $(id)", "--wait"]);
  assert.deepEqual(await readdir(cwd), [], "the shell created nothing");
});

test("the prompt's rebase hint quotes the base, and its command is the quoted one", async () => {
  const decision = { kind: "reject", request_revision: true, comment: "split it", reviewer: "human:a", comments: [] };
  const prompt = followUpPrompt({ task: branchTask, decision, reviewer: "Ada", command: proposeAgainCommand(branchTask, "/g") });
  assert.match(prompt, /git rebase -i "0123456789;i"/);
  assert.ok(prompt.includes(`node /g/propose-branch.mjs "0123456789;i..agent/x;touch\\\${IFS}pwned" --by "claude \\$(id)" --wait`));
});

test("a change's command quotes its summary", async () => {
  const cwd = await mkdtemp(join(tmpdir(), "chap-words-"));
  const command = proposeAgainCommand({ task_id: "t", output: { summary: "fix `rm -rf ~`", patch: "" } }, "/g");
  assert.deepEqual(await wordsOf(command, cwd), ["/g/propose.mjs", "fix `rm -rf ~`", "--wait", "--commit"]);
});

test("the desk's prompt for the agent quotes the branch and the base", async () => {
  const { createChapServer } = await import("../index.mjs");
  const { makeClient } = await import("../desk/chap-client.mjs");
  const { openDesk } = await import("./desk-harness.mjs");
  const { AGENT, HUMAN, project } = await import("./helpers.mjs");
  const { configPath, dir } = await project();
  const chap = await createChapServer({ configPath });
  const { base } = await chap.listen({ port: 0, host: "127.0.0.1", log: () => {} });
  const agent = makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: AGENT });
  const { task_id } = await agent.call("task.create", { kind: "commit_range", assignee: AGENT, input: { summary: "a branch" }, review_required: true });
  await agent.call("task.complete", { task_id, output: branchTask.output });
  await makeClient({ url: `${base}/chap`, workspace: "wsp_test", from: HUMAN }).call("decide.reject", { task_id, comment: "split it", request_revision: true });
  const desk = await openDesk(base, { hash: `#task=${task_id}` });
  try {
    assert.ok(await desk.until(() => desk.text("r-prompt")));
    const line = desk.text("r-prompt").split("\n").find((l) => l.startsWith("node "));
    assert.equal(line, `node ${dir}/propose-branch.mjs "0123456789;i..agent/x;touch\\\${IFS}pwned" --by "claude \\$(id)" --wait`);
  } finally {
    desk.close();
    await chap.close();
  }
});
