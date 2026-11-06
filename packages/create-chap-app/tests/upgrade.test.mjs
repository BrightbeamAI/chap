// Upgrade: a project a release wrote comes up to this one. What it still
// holds as the release wrote it is replaced or removed; what anyone changed
// stays and is named; the packages, the scripts and the stamp move forward.
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { appendFile, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { VERSION, generate, listTemplates } from "../index.mjs";
import { contentHash, loadReleased } from "../lib/released.mjs";
import { planUpgrade, recognise, runUpgrade } from "../lib/upgrade.mjs";
import { RELEASES, releasedProject } from "./released.mjs";

const scratch = (name) => mkdtemp(join(tmpdir(), `cca-up-${name}-`));

async function walk(dir, prefix = "") {
  const out = [];
  for (const d of await readdir(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${d.name}` : d.name;
    if (d.isDirectory()) out.push(...(await walk(join(dir, d.name), rel)));
    else out.push(rel);
  }
  return out;
}

async function snapshot(dir) {
  const out = {};
  for (const rel of await walk(dir)) out[rel] = contentHash(await readFile(join(dir, rel)));
  return out;
}

async function upgrade(argv) {
  const lines = [];
  const code = await runUpgrade(argv, { say: (t) => lines.push(t) });
  return { code, text: lines.join("\n") };
}

test("the copies of the release this clone records are the ones its tags hold", (t) => {
  const tags = execFileSync("git", ["tag", "-l", "v0.3.*"], { cwd: fileURLToPath(new URL("../../../", import.meta.url)), encoding: "utf8" }).split("\n").filter(Boolean);
  if (!RELEASES.every((v) => tags.includes(`v${v}`))) return t.skip("this clone has not got the release tags; git fetch --tags brings them");
  const script = fileURLToPath(new URL("../scripts/released-copies.mjs", import.meta.url));
  assert.match(execFileSync(process.execPath, [script, "--check"], { encoding: "utf8" }), /matches the release tags/);
  assert.deepEqual(loadReleased().versions, RELEASES);
});

test("a 0.3.4 code gate comes up to this release, and what its owner changed stays", async () => {
  const dir = await releasedProject("0.3.4", "code-gate", join(await scratch("cg"), "my-gate"));
  // The owner's own changes: the agent, one of the copied library files, a script.
  await appendFile(join(dir, "agent.mjs"), "\n// the owner's change\n");
  await appendFile(join(dir, "lib", "gate.mjs"), "\n// the owner's change\n");
  const pkg = JSON.parse(await readFile(join(dir, "package.json"), "utf8"));
  pkg.scripts.demo = "node demo.mjs --fresh";
  await writeFile(join(dir, "package.json"), `${JSON.stringify(pkg, null, 2)}\n`);
  const configBefore = await readFile(join(dir, "chap.config.json"), "utf8");

  const { code, text } = await upgrade([dir]);
  assert.equal(code, 0);
  assert.match(text, /recognised as the code-gate template from create-chap-app 0\.3\.4/);

  // Everything the owner did not change is as this release writes it.
  const fresh = join(await scratch("fresh"), "p");
  const config = JSON.parse(configBefore);
  await generate({ name: "my-code-gate", template: "code-gate", profiles: config.profiles, workspace: config.workspace, human_uri: config.humans[0].uri, human_name: config.humans[0].display_name, human_email: config.humans[0].email, agent_uri: config.agent.uri, review_at: config.review_at, project_dir: dir }, { targetDir: fresh });
  const kept = new Set(["agent.mjs", "README.md", "AGENT_INSTRUCTIONS.md", "chap.config.json", "package.json"]);
  for (const rel of await walk(fresh)) {
    if (kept.has(rel)) continue;
    assert.equal(contentHash(await readFile(join(dir, rel))), contentHash(await readFile(join(fresh, rel))), `${rel} is this release's`);
  }
  // The copies now in the packages are gone; the changed one stays, and so do the hooks.
  const files = await walk(dir);
  assert.ok(!files.some((f) => f.startsWith("desk/")), "the desk comes from @brightbeamai/chap-server");
  assert.deepEqual(files.filter((f) => f.startsWith("lib/")), ["lib/gate.mjs"]);
  assert.match(text, /Kept, no longer in the template, since each differs from every released copy:\n {2}lib\/gate\.mjs/);
  assert.match(text, /Kept, since each differs[^\n]*\n(?: {2}\S+\n)* {2}agent\.mjs/);
  assert.match(await readFile(join(dir, "agent.mjs"), "utf8"), /the owner's change/);
  for (const hook of ["pre-commit", "commit-msg", "post-commit", "pre-push"]) assert.ok(files.includes(`hooks/${hook}`), hook);

  // Packages and scripts forward, the owner's script kept; the stamp set and the rest of the configuration as it was.
  const after = JSON.parse(await readFile(join(dir, "package.json"), "utf8"));
  assert.equal(after.dependencies["@brightbeamai/chap-server"], `^${VERSION}`);
  assert.equal(after.dependencies["@brightbeamai/chap-code-gate"], `^${VERSION}`);
  assert.equal(after.scripts.keys, "chap-server keys");
  assert.equal(after.scripts.demo, "node demo.mjs --fresh");
  assert.match(text, /Scripts kept as they are[^\n]*\n {2}demo: the template's is "node demo\.mjs"/);
  const configAfter = await readFile(join(dir, "chap.config.json"), "utf8");
  assert.deepEqual(JSON.parse(configAfter).template, { name: "code-gate", version: VERSION });
  assert.equal(configAfter.replace(/ {2}"template": \{[^}]*\},\n/, ""), configBefore, "only the stamp is new");
  assert.match(text, /The template's differ in:\n {2}require_signatures: "humans" \(yours: true\)/);

  // A second run finds nothing to do.
  const again = await upgrade([dir]);
  assert.match(again.text, /^[^\n]*: the code-gate template from create-chap-app [\d.]+, brought up to/);
  assert.match(again.text, /Nothing to bring forward/);
});

test("a project from every release is recognised without a stamp", async () => {
  const root = await scratch("which");
  for (const version of RELEASES) {
    for (const t of await listTemplates()) {
      const dir = await releasedProject(version, t.name, join(root, `${t.name}-${version}`));
      const found = await recognise(dir);
      assert.equal(found?.name, t.name, `${t.name} from ${version}`);
      assert.equal(found.version, version, `${t.name} from ${version}`);
      await rm(dir, { recursive: true, force: true });
    }
  }
});

test("--dry-run says what would change and changes nothing; --diff shows a kept file's difference", async () => {
  const dir = await releasedProject("0.3.2", "mcp-gate", join(await scratch("dry"), "gate"));
  await appendFile(join(dir, "server.mjs"), "// mine\n");
  const before = await snapshot(dir);
  const { text } = await upgrade([dir, "--dry-run", "--diff"]);
  assert.deepEqual(await snapshot(dir), before);
  assert.match(text, /To remove, each as a release wrote it/);
  assert.match(text, /Nothing was changed: --dry-run\./);
  assert.match(text, /^diff --git yours\/server\.mjs template\/server\.mjs$/m);
  assert.match(text, /^-\/\/ mine$/m, "the line only the project has");
  assert.match(text, /port: 8792 \(yours: 8787\)/);
});

test("a Python project's chap- requirements take the template's later pins, and the rest stays", async () => {
  const dir = await releasedProject("0.3.1", "support-desk", join(await scratch("py"), "desk"));
  await writeFile(join(dir, "requirements.txt"), "chap-coordinator[crypto]>=0.3.0,<0.4\nrequests>=2.31\n");
  const pin = (await readFile(new URL("../templates/support-desk/requirements.txt", import.meta.url), "utf8")).split("\n").find((l) => l.startsWith("chap-coordinator"));
  const { text } = await upgrade([dir]);
  assert.equal(await readFile(join(dir, "requirements.txt"), "utf8"), `${pin}\nrequests>=2.31\n`);
  assert.match(text, /chap-coordinator: chap-coordinator\[crypto\]>=0\.3\.0,<0\.4 -> /);
  assert.match(text, /Next: python -m pip install -r requirements\.txt/);
  assert.deepEqual(JSON.parse(await readFile(join(dir, "chap.config.json"), "utf8")).template, { name: "support-desk", version: VERSION });
  // A pin later than the template's is never moved back.
  await writeFile(join(dir, "requirements.txt"), "chap-coordinator[crypto]>=9.0.0\nrequests>=2.31\n");
  await upgrade([dir]);
  assert.equal(await readFile(join(dir, "requirements.txt"), "utf8"), "chap-coordinator[crypto]>=9.0.0\nrequests>=2.31\n");
});

test("a released hook no longer in the template stays whatever it holds", async () => {
  const dir = await releasedProject("0.3.4", "code-gate", join(await scratch("hooks"), "gate"));
  await writeFile(join(dir, "hooks", "pre-rebase"), "#!/bin/sh\nexit 0\n");
  const released = loadReleased();
  const known = structuredClone(released);
  known.templates["code-gate"].files["hooks/pre-rebase"] = { [contentHash("#!/bin/sh\nexit 0\n")]: ["0.3.4"] };
  const plan = await planUpgrade(dir, { released: known });
  try {
    assert.deepEqual(plan.files.keptByRule, ["hooks/pre-rebase"]);
    assert.ok(!plan.files.remove.includes("hooks/pre-rebase"));
  } finally {
    await rm(plan.scratch, { recursive: true, force: true });
  }
});

test("a directory create-chap-app did not write is refused, with what to do", async () => {
  const dir = await scratch("none");
  await assert.rejects(upgrade([dir]), /has no chap\.config\.json/);
  await writeFile(join(dir, "chap.config.json"), "{}\n");
  await assert.rejects(upgrade([dir]), /no template stamp, and no file .* Name the template it came from with --template/);
});
