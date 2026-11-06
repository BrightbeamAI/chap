// This project's entry points run whatever path reaches them. Node gives a
// module its real path and gives the script as typed, so a check that
// compared the two did nothing through a symbolic link, and a hook reached
// that way let an unreviewed commit through.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { git } from "@brightbeamai/chap-code-gate/git";
import { projectDir, run } from "./helpers.mjs";

async function linkedProject() {
  const link = join(await mkdtemp(join(tmpdir(), "chap-link-")), "gate");
  await symlink(projectDir, link);
  return link;
}

test("through a symbolic link to the project, the hooks still check, and refuse a commit nobody approved", async () => {
  const link = await linkedProject();
  const repo = await mkdtemp(join(tmpdir(), "chap-linked-repo-"));
  await git(repo, ["init", "-q", "-b", "main"]);
  await git(repo, ["config", "user.email", "a@example.org"]);
  await git(repo, ["config", "user.name", "A"]);
  await writeFile(join(repo, "f.txt"), "one\n");
  await git(repo, ["add", "f.txt"]);
  await git(repo, ["-c", "core.hooksPath=/dev/null", "commit", "-qm", "base"]);
  await git(repo, ["config", "core.hooksPath", join(link, "hooks")]);
  await writeFile(join(repo, "f.txt"), "one\ntwo\n");
  const env = { ...process.env, CHAP_CONFIG: join(projectDir, "chap.config.json"), CHAP_AUTOSTART: "0", CHAP_NO_BROWSER: "1", PORT: "1" };
  delete env.CHAP_URL;
  await assert.rejects(run("git", ["commit", "-qam", "unreviewed"], { cwd: repo, env }), (e) => /chap: /.test(e.stderr));
  assert.equal((await git(repo, ["rev-list", "--count", "HEAD"])).trim(), "1", "nothing was committed");
});

test("through a symbolic link, server.mjs serves the desk", async () => {
  const link = await linkedProject();
  const child = spawn(process.execPath, [join(link, "server.mjs")], { env: { ...process.env, PORT: "0", CHAP_DB_PATH: ":memory:" }, stdio: ["ignore", "pipe", "pipe"] });
  try {
    const base = await new Promise((resolve, reject) => {
      let out = "";
      child.stdout.on("data", (d) => { out += d; const m = /on (http:\/\/\S+)/.exec(out); if (m) resolve(m[1]); });
      child.on("exit", (code) => reject(new Error(`server.mjs exited ${code} with nothing served: ${out}`)));
    });
    assert.equal((await (await fetch(`${base}/api/health`)).json()).ok, true);
  } finally {
    child.kill();
  }
});

test("through a symbolic link, agent.mjs runs and says what is wrong", async () => {
  const link = await linkedProject();
  await assert.rejects(run(process.execPath, [join(link, "agent.mjs"), "--bogus"]), (e) => e.code === 1 && /Unknown option --bogus/.test(e.stderr));
});
