// The model that wrote an agent's work, as the agent's harness records it:
// Claude Code's Co-Authored-By line on each commit, the session's model that
// its SessionStart hook hands to the commands it runs, and the agent's own
// word last.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { Readable } from "node:stream";
import { exportLine, main as sessionHook } from "../claude-session.mjs";
import { CLAUDE_SESSION_HOOK, installClaudeHook } from "../install-hooks.mjs";
import { git } from "../lib/git.mjs";
import { branchModel, commitModel, sessionModel, withoutAttribution } from "../lib/model.mjs";
import { modelName } from "../lib/providers.mjs";

test("a commit's own Co-Authored-By line names its model and leaves the message; a person's line stays", () => {
  const message = "Fix the parser\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>";
  assert.equal(commitModel(message), "Claude Opus 5.5");
  assert.equal(withoutAttribution(message).trimEnd(), "Fix the parser");
  assert.equal(commitModel("x\n\nCo-authored-by: Claude Sonnet 4.5 <noreply@anthropic.com>"), "Claude Sonnet 4.5");
  assert.equal(commitModel("x\n\nCo-Authored-By: Claude <noreply@anthropic.com>"), null, "Claude alone names no model");
  assert.equal(commitModel("x\n\nCo-Authored-By: Claude Code <noreply@anthropic.com>"), null);
  assert.equal(commitModel("x\n\nCo-Authored-By: Ada Lovelace <ada@example.org>"), null);
  assert.equal(withoutAttribution("x\n\nCo-Authored-By: Ada Lovelace <ada@example.org>"), "x\n\nCo-Authored-By: Ada Lovelace <ada@example.org>");
});

test("a model id reads as its name, in a provider's form or with a context suffix too", () => {
  assert.equal(modelName("claude-opus-5-5"), "Claude Opus 5.5");
  assert.equal(modelName("claude-sonnet-4-5-20250929"), "Claude Sonnet 4.5");
  assert.equal(modelName("claude-sonnet-4-5[1m]"), "Claude Sonnet 4.5");
  assert.equal(modelName("us.anthropic.claude-sonnet-4-5-20250929-v1:0"), "Claude Sonnet 4.5");
  assert.equal(modelName("claude-sonnet-4-5@20250929"), "Claude Sonnet 4.5");
  assert.equal(modelName("claude-3-5-sonnet-20241022"), "Claude 3.5 Sonnet");
  assert.equal(modelName("Claude Opus 5.5"), "Claude Opus 5.5");
});

test("the session's model comes before the agent's word, a difference is reported, and a branch's model is its commits'", () => {
  assert.deepEqual(sessionModel({ env: { CHAP_MODEL: "claude-opus-5-5" } }), { model: "Claude Opus 5.5", source: "session", differs: null });
  assert.deepEqual(sessionModel({ flag: "Claude Sonnet 5.5", env: { CHAP_MODEL: "claude-opus-5-5" } }), { model: "Claude Opus 5.5", source: "session", differs: "Claude Sonnet 5.5" });
  assert.deepEqual(sessionModel({ flag: "Claude Fable 5.1", env: {} }), { model: "Claude Fable 5.1", source: "agent", differs: null });
  assert.deepEqual(sessionModel({ env: {} }), { model: null, source: null, differs: null });
  assert.throws(() => sessionModel({ flag: "a <model>", env: {} }), /Not a model name/);
  assert.deepEqual(branchModel([{ model: "Claude Opus 5.5" }, { model: "Claude Opus 5.5" }], { model: "Claude Fable 5.1", source: "agent" }), { model: "Claude Opus 5.5", source: "commits" });
  assert.deepEqual(branchModel([{ model: "Claude Opus 5.5" }, {}], { model: "Claude Fable 5.1", source: "session" }), { model: "Claude Fable 5.1", source: "session" });
  assert.deepEqual(branchModel([{ model: "Claude Opus 5.5" }, { model: "Claude Sonnet 5.5" }, {}], null), { model: "Claude Opus 5.5 and Claude Sonnet 5.5", source: "commits" });
  assert.deepEqual(branchModel([{}, {}], null), { model: null, source: null });
});

test("Claude Code's SessionStart hook hands the session's model to its commands, and writes nothing it cannot vouch for", async () => {
  const file = join(await mkdtemp(join(tmpdir(), "chap-env-")), "env");
  await sessionHook({ stdin: Readable.from([JSON.stringify({ hook_event_name: "SessionStart", source: "startup", model: "claude-opus-5-5" })]), env: { CLAUDE_ENV_FILE: file } });
  assert.equal(await readFile(file, "utf8"), "export CHAP_MODEL='claude-opus-5-5'\n");
  assert.equal(exportLine({ id: "claude-sonnet-4-5[1m]" }), "export CHAP_MODEL='claude-sonnet-4-5[1m]'\n");
  assert.equal(exportLine("x'; touch /tmp/owned; '"), null);
  assert.equal(exportLine(undefined), null);
  await sessionHook({ stdin: Readable.from(["not json"]), env: { CLAUDE_ENV_FILE: file } });
  await sessionHook({ stdin: Readable.from([JSON.stringify({ source: "clear" })]), env: { CLAUDE_ENV_FILE: file } });
  await sessionHook({ stdin: Readable.from([JSON.stringify({ model: "claude-opus-5-5" })]), env: {} });
  assert.equal(await readFile(file, "utf8"), "export CHAP_MODEL='claude-opus-5-5'\n", "nothing more was written");
});

test("install-hooks --claude registers the hook once, keeps the other settings, and keeps the file out of git", async () => {
  const repo = await mkdtemp(join(tmpdir(), "chap-claude-"));
  await git(repo, ["init", "-q"]);
  await mkdir(join(repo, ".claude"));
  await writeFile(join(repo, ".claude", "settings.local.json"), JSON.stringify({ permissions: { allow: ["Bash(npm test)"] } }));
  const quiet = { log: () => {} };
  assert.equal((await installClaudeHook(repo, quiet)).added, true);
  assert.equal((await installClaudeHook(repo, quiet)).added, false, "a second run changes nothing");
  const settings = JSON.parse(await readFile(join(repo, ".claude", "settings.local.json"), "utf8"));
  assert.deepEqual(settings.permissions, { allow: ["Bash(npm test)"] });
  assert.deepEqual(settings.hooks.SessionStart, [{ hooks: [{ type: "command", command: CLAUDE_SESSION_HOOK }] }]);
  assert.match(await readFile(join(repo, ".git", "info", "exclude"), "utf8"), /^\/\.claude\/settings\.local\.json$/m);
  assert.equal((await git(repo, ["status", "--porcelain"])).trim(), "", "git does not see the file");
  await writeFile(join(repo, ".claude", "settings.local.json"), "{ not json");
  await assert.rejects(installClaudeHook(repo, quiet), /does not read as JSON/);
});
