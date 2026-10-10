// The review loop an agent runs: one command starts the gate and brings
// the review to the reviewer, waits within the time an agent's tool call
// is allowed, and turns the reviewer's decision, with their comments on
// lines, into a prompt the agent acts on; a revision shows what changed
// since the last look.
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createServer } from "node:net";
import { followUpPrompt } from "../desk/followup.js";
import { deskIsOpen, ensureGate, loadGate, showReview } from "../lib/gate.mjs";
import { git } from "../lib/git.mjs";
import { commandAgain, withNote } from "../lib/loop.mjs";
import { describeRange } from "../lib/range.mjs";
import { appendTo, decide, demoRepo, hookEnv, projectDir, reviewsFor, run, startGate } from "./helpers.mjs";

let g, you, repo, base;
const sh = (cmd, args, env = {}) => run(cmd, args, { cwd: repo, env: { ...hookEnv(g), ...env } });
const branchCommand = (extra = []) => [join(projectDir, "propose-branch.mjs"), `${base}..agent/loop`, "--by", "Claude Code", "--model", "Claude Opus 5.5", ...extra];

before(async () => {
  process.env.CHAP_NO_BROWSER = "1";
  g = await startGate({ suffix: "loop", extra: { review_at: "push" } });
  you = await g.reviewer(g.config.humans[0].uri);
  repo = await demoRepo();
  base = (await git(repo, ["rev-parse", "HEAD"])).trim();
});

after(async () => { await g.close(); });

test("the prompt for each kind of decision says what was decided, quotes the note and the comments, and gives the command to run again", () => {
  const task = { task_id: "tsk_X", input: {}, output: { summary: "Add subtract", repo: "calc", branch: "agent/x", base: "abc123def4567890", commits: [{ sha: "1234567aaa" }] } };
  const changes = followUpPrompt({
    task, reviewer: "Ada Lovelace", command: "node propose-branch.mjs abc..agent/x --wait",
    decision: { kind: "reject", request_revision: true, comment: "Tests first.", comments: [{ path: "lib/calc.mjs", line: 12, side: "new", code: "export function subtract(a, b) {", text: "Name them minuend and subtrahend.", commit: "1234567aaa", commit_index: 1, commit_of: 1 }] },
  });
  assert.match(changes, /^Ada Lovelace reviewed "Add subtract" \(repository calc, branch agent\/x, review tsk_X\) and asked for changes\./);
  assert.match(changes, /^> Tests first\.$/m);
  assert.match(changes, /^- lib\/calc\.mjs, line 12, commit 1 of 1, 1234567:$/m);
  assert.match(changes, /^ {2}export function subtract\(a, b\) \{$/m);
  assert.match(changes, /^ {2}Name them minuend and subtrahend\.$/m);
  assert.match(changes, /git rebase -i abc123def456/);
  assert.match(changes, /```bash\nnode propose-branch\.mjs abc\.\.agent\/x --wait\n```/);
  const rejected = followUpPrompt({ task, decision: { kind: "reject", comment: "Not now." } });
  assert.match(rejected, /and rejected it\.[\s\S]*do not propose it again as it stands/);
  assert.equal(followUpPrompt({ task, decision: { kind: "approve" } }), null, "an approval as written asks nothing");
  assert.match(followUpPrompt({ task, decision: { kind: "approve", comments: [{ path: "a", line: 1, text: "Later, rename this." }] } }), /with comments to act on next/);
  // An edit: what the reviewer changed in the agent's version.
  const edit = followUpPrompt({
    task: {
      task_id: "tsk_Y", input: {},
      output: { summary: "Sum", files: [{ path: "lib/calc.mjs", after: "export const sum = (a, b) => a + b; // the sum\n" }], patch: "" },
      submission: { envelope: { method: "task.complete", params: { output: { summary: "Sum", files: [{ path: "lib/calc.mjs", after: "export const sum = (a, b) => a + b;\n" }], patch: "" } } } },
    },
    decision: { kind: "override", rationale: "Say what it returns." },
  });
  assert.match(edit, /approved it with an edit of their own/);
  assert.match(edit, /^> Say what it returns\.$/m);
  assert.match(edit, /^-export const sum = \(a, b\) => a \+ b;$/m);
  assert.match(edit, /^\+export const sum = \(a, b\) => a \+ b; \/\/ the sum$/m);
  assert.equal(commandAgain("/tmp/gate dir/propose.mjs", ["Fix it", "--wait"]), 'node "/tmp/gate dir/propose.mjs" "Fix it" --wait');
  assert.equal(withNote("Approved by Ada Lovelace", "Read all three."), "Approved by Ada Lovelace: Read all three.");
  assert.equal(withNote("Approved by Ada Lovelace", " Good "), "Approved by Ada Lovelace: Good.");
  assert.equal(withNote("Approved by Ada Lovelace", ""), "Approved by Ada Lovelace.");
});

test("a branch with a context note waits for the reviewer, comes back with their prompt, and a revision shows what changed since", async () => {
  await git(repo, ["checkout", "-q", "-b", "agent/loop", base]);
  await appendTo(repo, "lib/calc.mjs", "\nexport function subtract(a, b) {\n  return a - b;\n}\n");
  await sh("git", ["commit", "-q", "-am", "Add subtract"]);
  const note = join(await mkdtemp(join(tmpdir(), "ctx-")), "context.md");
  await writeFile(note, "## What was asked\nAdd subtract.\n\n## How it was tested\n`npm test`: all pass.\n");
  // Waiting within a time limit hands back exit code 4 with the command to run again.
  await assert.rejects(sh(process.execPath, branchCommand(["--context", note, "--wait", "--timeout", "0.02"])), (e) => e.code === 4 && /Still waiting for the review of tsk_\w+\. Run the same command again to keep waiting:/.test(e.stdout) && /--timeout 0\.02/.test(e.stdout));
  const review = (await reviewsFor(g, you)).find((r) => r.artefact.branch === "agent/loop");
  assert.equal(review.artefact.context, await readFile(note, "utf8").then((t) => t.trimEnd()));
  // The reviewer comments on a line and asks for changes; the command, run again, prints the prompt.
  const line = { path: "lib/calc.mjs", line: 12, side: "new", code: "export function subtract(a, b) {", text: "Name the parameters minuend and subtrahend.", commit: review.artefact.commits[0].sha, commit_index: 1, commit_of: 1 };
  await decide(you, "decide.reject", review, { comment: "One thing to fix.", request_revision: true, comments: [line] });
  const view = await (await fetch(`${g.base}/api/tasks/${review.task_id}`)).json();
  assert.deepEqual(view.decision_log.at(-1).comments, [line], "the comments are on the chain with the decision");
  const back = await sh(process.execPath, branchCommand(["--context", note, "--wait"])).catch((e) => e);
  assert.equal(back.code, 3);
  assert.match(back.stdout, /---- The review, for the agent to act on ----/);
  assert.match(back.stdout, /^> One thing to fix\.$/m);
  assert.match(back.stdout, /- lib\/calc\.mjs, line 12, commit 1 of 1, \w{7}:/);
  assert.match(back.stdout, /Name the parameters minuend and subtrahend\./);
  assert.match(back.stdout, /propose-branch\.mjs \w+\.\.agent\/loop --by "Claude Code" --model "Claude Opus 5\.5" --context \S+ --wait/);
  // The agent amends; the revision carries what changed since the reviewer looked.
  await writeFile(join(repo, "lib/calc.mjs"), (await readFile(join(repo, "lib/calc.mjs"), "utf8")).replace("subtract(a, b) {\n  return a - b;", "subtract(minuend, subtrahend) {\n  return minuend - subtrahend;"));
  await sh("git", ["commit", "-q", "--amend", "-a", "--no-edit"]);
  const revised = await sh(process.execPath, branchCommand(["--context", note])).catch((e) => e);
  assert.match(revised.stdout, new RegExp(`Revised ${review.task_id}`));
  const again = (await reviewsFor(g, you)).find((r) => r.task_id === review.task_id);
  assert.equal(again.artefact.since.head, review.artefact.head);
  assert.match(again.artefact.since.patch, /^-export function subtract\(a, b\) \{$/m);
  assert.match(again.artefact.since.patch, /^\+export function subtract\(minuend, subtrahend\) \{$/m);
  // The commits carry the files whole, so the desk can show the code around each change.
  assert.equal(typeof again.artefact.commits[0].files[0].after, "string");
  const brief = (await (await fetch(`${g.base}/api/tasks?kind=commit_range&brief=1`)).json()).tasks.find((t) => t.task_id === review.task_id);
  assert.equal(brief.artefact.context, undefined);
  assert.equal(brief.artefact.commits[0].files[0].after, undefined);
  assert.equal(brief.artefact.since.patch, undefined);
  await git(repo, ["checkout", "-q", "main"]);
});

test("a desk that is open is shown the review, and the browser is opened only when none is", async () => {
  const shown = await showReview(g.gate, "tsk_Z");
  assert.equal(shown.how, "none", "with CHAP_NO_BROWSER=1 and no desk open, the address is given");
  await fetch(`${g.base}/api/health?desk=1`);
  assert.equal(await deskIsOpen(g.gate), true);
  assert.equal((await showReview(g.gate, "tsk_Z")).how, "desk");
});

test("a command finds no gate running on this machine, starts it in the background, and npm run stop stops it", async () => {
  const port = await new Promise((resolve) => { const s = createServer().listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => resolve(p)); }); });
  const dir = await mkdtemp(join(tmpdir(), "gate-cfg-"));
  const configPath = join(dir, "chap.config.json");
  await writeFile(configPath, JSON.stringify({ ...g.config, workspace: "wsp_autostart", port, store: ":memory:" }));
  const env = { CHAP_CONFIG: process.env.CHAP_CONFIG, CHAP_URL: process.env.CHAP_URL, CHAP_AUTOSTART: process.env.CHAP_AUTOSTART };
  process.env.CHAP_CONFIG = configPath;
  delete process.env.CHAP_URL;
  delete process.env.CHAP_AUTOSTART;
  try {
    const gate = await loadGate(projectDir);
    const lines = [];
    const cfg = await ensureGate(gate, { log: (l) => lines.push(l) });
    assert.equal(cfg.workspace, "wsp_autostart");
    assert.match(lines.join("\n"), /Started the gate at http:\/\/127\.0\.0\.1:\d+ in the background/);
    const pid = Number((await readFile(join(projectDir, "data", "gate.pid"), "utf8")).trim());
    assert.ok(pid > 0);
    const { stdout } = await run(process.execPath, [join(projectDir, "stop.mjs")]);
    assert.match(stdout, new RegExp(`Stopped the gate \\(process ${pid}\\)`));
  } finally {
    for (const [k, v] of Object.entries(env)) { if (v === undefined) delete process.env[k]; else process.env[k] = v; }
  }
});

test("a range with no commits is refused", async () => {
  await assert.rejects(describeRange(repo, { base, head: base }), /holds no commits/);
});
