// The first run: the prompt says which templates run on Python and what each
// profile adds, and the next steps it prints run in PowerShell and cmd as
// well as in a POSIX shell.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { PassThrough } from "node:stream";
import { PROFILE_NOTES, generate, listTemplates, main, nextSteps } from "../index.mjs";

/** Run the command with `answers` typed at its prompts, one per question. Returns what it wrote. */
async function run(argv, answers = [], interactive = answers.length > 0) {
  const input = new PassThrough();
  const output = new PassThrough();
  let text = "";
  const pending = [...answers];
  output.on("data", (chunk) => {
    text += chunk;
    if (/\] $/.test(text) && pending.length) input.write(`${pending.shift()}\n`);
  });
  const code = await main(argv, { input, output, interactive });
  return { code, text };
}

test("the prompt says which templates run on Python, and what each profile adds", async () => {
  const dir = await mkdtemp(join(tmpdir(), "cca-ask-"));
  await rm(dir, { recursive: true });
  const { code, text } = await run(["--dir", dir], ["support-desk", "", "desk"]);
  assert.equal(code, 0);
  for (const t of await listTemplates()) {
    const label = t.language === "python" ? "Python" : "Node.js";
    assert.match(text, new RegExp(`\\d\\. ${t.name} +${label} `), `${t.name} is listed as ${label}`);
  }
  const desk = (await listTemplates()).find((t) => t.name === "support-desk");
  for (const id of desk.profile_choices) assert.ok(text.includes(`  ${id.padEnd(20)} ${PROFILE_NOTES[id]}`), `${id} is explained`);
  assert.match(text, /The desk is at http:\/\/127\.0\.0\.1:8788\/ once it runs; open it in a browser\./);
  await rm(dir, { recursive: true });
});

test("every profile a template offers has a line in the prompt", async () => {
  for (const t of await listTemplates()) {
    for (const id of t.profile_choices) assert.ok(PROFILE_NOTES[id], `${t.name} offers ${id}, which the prompt explains`);
  }
});

test("--list and --help say what each template needs installed", async () => {
  for (const flag of ["--list", "--help"]) {
    const { text } = await run([flag]);
    assert.match(text, /code-gate +Node\.js /, flag);
    assert.match(text, /support-desk +Python /, flag);
  }
});

test("the next steps for a Node.js template are commands Windows has too, and name the desk's address", async () => {
  for (const t of (await listTemplates()).filter((x) => x.language === "typescript")) {
    const dir = await mkdtemp(join(tmpdir(), `cca-next-${t.name}-`));
    await rm(dir, { recursive: true });
    const result = await generate({ name: `my-${t.name}`, template: t.name, profiles: t.default_profiles }, { targetDir: dir });
    const steps = nextSteps(result);
    for (const step of t.run) assert.match(step, /^(npm|npx|node|docker) /, `${t.name}: ${step}`);
    assert.doesNotMatch(steps, /^ *(open|mkdir|xdg-open) /m, t.name);
    assert.ok(steps.includes(`The desk is at http://127.0.0.1:${result.config.port}/`), t.name);
    await rm(dir, { recursive: true });
  }
  const spaced = nextSteps({ target: "/home/Ada Lovelace/my-gate", manifest: { run: [] }, config: null }, { relative: false });
  assert.match(spaced, /^ {2}cd "\/home\/Ada Lovelace\/my-gate"$/m, "a path with a space is quoted");
});

test("the production project ships the directories its containers mount, kept by git", async () => {
  const t = (await listTemplates()).find((x) => x.name === "production");
  const dir = await mkdtemp(join(tmpdir(), "cca-dirs-"));
  await rm(dir, { recursive: true });
  await generate({ name: "prod", template: "production", profiles: t.default_profiles }, { targetDir: dir });
  for (const d of ["data", "outbox", "analytics"]) assert.deepEqual(await readdir(join(dir, d)), [".gitkeep"], d);
  const ignore = await readFile(join(dir, ".gitignore"), "utf8");
  for (const d of ["data", "outbox", "analytics"]) {
    assert.match(ignore, new RegExp(`^${d}/\\*$`, "m"));
    assert.match(ignore, new RegExp(`^!${d}/\\.gitkeep$`, "m"));
  }
  await rm(dir, { recursive: true });
});
