// Every command this project offers says how it is run with --help, and an
// option it does not know is refused with a pointer to that help.
import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { projectDir, run } from "./helpers.mjs";

const node = (args) => run(process.execPath, args, { cwd: projectDir, timeout: 10000 });
const bin = join(projectDir, "node_modules", "@brightbeamai", "chap-code-gate", "bin", "chap-code-gate.mjs");

test("each script and command prints its help with --help", async () => {
  const usage = {
    "server.mjs": /npm start/, "propose.mjs": /propose "what it does"/, "propose-branch.mjs": /<base>\.\.\]<head>/, "verify.mjs": /verify \[range\]/,
    "install-hooks.mjs": /install-hooks \[--repo <path>\]/, "push-notes.mjs": /push-notes \[--repo <path>\]/, "agent.mjs": /--branch <name>/,
    "demo.mjs": /--fresh/, "demo-repo.mjs": /demo-repo/, "diff-profiles.mjs": /--against c,d/,
  };
  for (const [script, pattern] of Object.entries(usage)) {
    const { stdout } = await node([join(projectDir, script), "--help"]);
    assert.match(stdout, pattern, script);
  }
  for (const [command, pattern] of [["trust", /trust \[options\]/], ["report", /report \[--json\]/], ["stop", /chap-code-gate stop/], ["hook", /hook <pre-commit/]]) {
    const { stdout } = await node([bin, command, "--help", "--gate", projectDir]);
    assert.match(stdout, pattern, command);
  }
});

test("an option a command does not know is refused, with a pointer to --help", async () => {
  for (const script of ["propose.mjs", "verify.mjs", "agent.mjs", "demo.mjs"]) {
    await assert.rejects(node([join(projectDir, script), "--bogus"]), (e) => /Unknown option --bogus\. --help lists the options\./.test(e.stderr), script);
  }
});
