// The generator: every template generates with no placeholder left behind,
// the shared files land where the manifest says, and the flags are parsed.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DEFAULT_TEMPLATE, generate, gitIdentity, listTemplates, parseArgs } from "../index.mjs";

async function walk(dir, prefix = "") {
  const out = [];
  for (const d of await readdir(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${d.name}` : d.name;
    if (d.isDirectory()) out.push(...(await walk(join(dir, d.name), rel)));
    else out.push(rel);
  }
  return out;
}

test("every template generates with its placeholders filled", async () => {
  const templates = await listTemplates();
  assert.ok(templates.length >= 4);
  for (const t of templates) {
    const dir = await mkdtemp(join(tmpdir(), `cca-${t.name}-`));
    await rm(dir, { recursive: true });
    const { written, fill } = await generate({ name: `my-${t.name}`, template: t.name, profiles: t.default_profiles }, { targetDir: dir });
    assert.ok(written.includes("README.md"), `${t.name} has a README`);
    assert.ok(written.includes("chap.config.json"), `${t.name} has chap.config.json`);
    assert.ok(written.includes("desk/index.html") && written.includes("desk/chap-client.mjs"), `${t.name} has the desk`);
    assert.ok(written.includes(".gitignore"), `${t.name} has a .gitignore, renamed from the shipped gitignore`);
    assert.ok(!written.some((w) => /(^|\/)(gitignore|dockerignore)$/.test(w)), `${t.name} leaves no plain ignore file`);
    for (const rel of await walk(dir)) {
      const text = await readFile(join(dir, rel), "utf8").catch(() => "");
      assert.doesNotMatch(text, /__[A-Z_]+__/, `${t.name}/${rel} has a placeholder left`);
    }
    const config = JSON.parse(await readFile(join(dir, "chap.config.json"), "utf8"));
    assert.equal(config.workspace, fill.__WORKSPACE__);
    assert.deepEqual(config.profiles, t.default_profiles);
    await rm(dir, { recursive: true });
  }
});

test("the package ships what a generated project needs", async () => {
  // npm leaves .gitignore out of a tarball, so the templates ship it as
  // `gitignore`, and the files list carries the bin, the templates, the
  // generator and the licence. This reads the files list the way npm pack does.
  const pkg = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
  assert.deepEqual(pkg.files, ["bin", "index.mjs", "templates", "README.md", "DESIGN.md", "LICENSE"]);
  assert.match(await readFile(new URL("../LICENSE", import.meta.url), "utf8"), /Apache License\s+Version 2\.0/);
  assert.equal(pkg.license, "Apache-2.0");
  assert.equal(pkg.scripts.prepublishOnly, "npm test", "a failing generator does not publish");
  assert.equal(pkg.bin["create-chap-app"], "bin/create-chap-app.mjs");
  const templates = await listTemplates();
  for (const t of templates) {
    const files = await walk(new URL(`../templates/${t.name}/`, import.meta.url).pathname);
    assert.ok(files.includes("gitignore"), `${t.name} ships gitignore without the dot`);
    assert.ok(!files.includes(".gitignore"), `${t.name} ships no .gitignore, which npm pack would drop`);
  }
});

test("generation refuses a directory that is not empty", async () => {
  const dir = await mkdtemp(join(tmpdir(), "cca-full-"));
  await generate({ name: "a", template: "mcp-gate", profiles: ["core/1.0"] }, { targetDir: dir });
  await assert.rejects(generate({ name: "a", template: "mcp-gate", profiles: ["core/1.0"] }, { targetDir: dir }), /not empty/);
  await rm(dir, { recursive: true });
});

test("the flags are parsed", () => {
  const a = parseArgs(["my-gate", "--template", "mcp-gate", "--profiles", "core/1.0, review/1.0", "--yes", "--dir", "/tmp/x"]);
  assert.equal(a.name, "my-gate");
  assert.equal(a.template, "mcp-gate");
  assert.deepEqual(a.profiles, ["core/1.0", "review/1.0"]);
  assert.equal(a.yes, true);
  assert.equal(a.dir, "/tmp/x");
  assert.throws(() => parseArgs(["--bogus"]), /Unknown option/);
  const b = parseArgs(["g", "--human-name", "Ada Lovelace", "--human-email", "ada@example.org", "--review-at", "push"]);
  assert.deepEqual([b.humanName, b.humanEmail, b.reviewAt], ["Ada Lovelace", "ada@example.org", "push"]);
  assert.throws(() => parseArgs(["g", "--human-name", 'Ada "the" Lovelace']), /can go in a commit trailer/);
  assert.throws(() => parseArgs(["g", "--human-email", "not an email"]), /Not an email address/);
  assert.throws(() => parseArgs(["g", "--review-at", "never"]), /commit or push/);
  const me = gitIdentity();
  assert.ok(me.name === null || typeof me.name === "string");
});

test("the code gate's hooks stay executable and its workflow lands under .github", async () => {
  const { stat } = await import("node:fs/promises");
  const dir = await mkdtemp(join(tmpdir(), "cca-gate-"));
  await rm(dir, { recursive: true });
  const t = (await listTemplates()).find((x) => x.name === "code-gate");
  const { written } = await generate({ name: "my-gate", template: "code-gate", profiles: t.default_profiles }, { targetDir: dir });
  for (const hook of ["pre-commit", "commit-msg", "post-commit", "pre-push"]) {
    assert.ok(written.includes(`hooks/${hook}`), hook);
    assert.ok((await stat(join(dir, "hooks", hook))).mode & 0o100, `${hook} is executable`);
  }
  assert.ok(written.includes(".github/workflows/chap-verify.yml"));
  assert.ok(!written.some((w) => w.startsWith("github/")));
  const instructions = await readFile(join(dir, "AGENT_INSTRUCTIONS.md"), "utf8");
  assert.ok(instructions.includes(`${dir}/propose.mjs`), "the instructions name this project's propose.mjs");
  await rm(dir, { recursive: true });
});

test("the code gate names its reviewer by name and email, and reviews at commit or at push", async () => {
  const t = (await listTemplates()).find((x) => x.name === "code-gate");
  const dir = await mkdtemp(join(tmpdir(), "cca-who-"));
  await rm(dir, { recursive: true });
  await generate({ name: "who", template: "code-gate", profiles: t.default_profiles, human_name: "Ada Lovelace", human_email: "ada@example.org", review_at: "push" }, { targetDir: dir });
  const config = JSON.parse(await readFile(join(dir, "chap.config.json"), "utf8"));
  assert.deepEqual([config.humans[0].display_name, config.humans[0].email, config.review_at], ["Ada Lovelace", "ada@example.org", "push"]);
  assert.ok(config.max_envelope_bytes >= 16 * 1024 * 1024, "a branch fits in one review");
  await rm(dir, { recursive: true });
  await generate({ name: "who", template: "code-gate", profiles: t.default_profiles }, { targetDir: dir });
  assert.equal(JSON.parse(await readFile(join(dir, "chap.config.json"), "utf8")).review_at, "commit");
  await rm(dir, { recursive: true });
});

test("with no template named, the code gate is the one generated", async () => {
  assert.equal(DEFAULT_TEMPLATE, "code-gate");
  assert.ok((await listTemplates()).some((t) => t.name === DEFAULT_TEMPLATE));
});
