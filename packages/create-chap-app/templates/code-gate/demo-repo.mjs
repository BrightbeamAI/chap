// A small repository to put under the gate: `node demo-repo.mjs [--dir <path>]`.
//
// Writes a tiny Node library with one function, a test and a README, makes
// the first commit, and points the repository's hooks at this gate. The
// sample tasks in tasks.csv are written for it, so the built-in agent can
// run against it with no model and no key.

import { mkdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { git } from "./lib/git.mjs";
import { installHooks } from "./install-hooks.mjs";

const here = dirname(fileURLToPath(import.meta.url));

export const FILES = {
  "package.json": JSON.stringify({ name: "calc", version: "0.1.0", private: true, type: "module", scripts: { test: "node --test" } }, null, 2) + "\n",
  "lib/calc.mjs": `// A calculator library, the subject of the gate's sample tasks.

export function add(a, b) {
  return a + b;
}

export function multiply(a, b) {
  return a * b;
}
`,
  "test/calc.test.mjs": `import { test } from "node:test";
import assert from "node:assert/strict";
import { add, multiply } from "../lib/calc.mjs";

test("add adds", () => {
  assert.equal(add(2, 3), 5);
});

test("multiply multiplies", () => {
  assert.equal(multiply(2, 3), 6);
});
`,
  "README.md": `# calc

A small calculator library used to show the CHAP code gate.

## Functions

- \`add(a, b)\`
- \`multiply(a, b)\`
`,
  ".gitignore": "node_modules/\n",
};

/** Create the repository at `dir`, commit the files, and install the gate's hooks. */
export async function createDemoRepo(dir, { log = console.log, hooks = true } = {}) {
  const repo = resolve(dir);
  await mkdir(repo, { recursive: true });
  await git(repo, ["init", "--quiet"]);
  await git(repo, ["symbolic-ref", "HEAD", "refs/heads/main"]);
  await git(repo, ["config", "user.name", "Demo Developer"]);
  await git(repo, ["config", "user.email", "developer@example.com"]);
  await git(repo, ["config", "commit.gpgsign", "false"]);
  for (const [path, body] of Object.entries(FILES)) {
    await mkdir(dirname(join(repo, path)), { recursive: true });
    await writeFile(join(repo, path), body);
  }
  await git(repo, ["add", "-A"]);
  await git(repo, ["commit", "--quiet", "-m", "Initial import of calc"]);
  if (hooks) await installHooks(repo, { log });
  log(`Demo repository at ${repo}, one commit on main.`);
  return repo;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const argv = process.argv.slice(2);
  const at = argv.indexOf("--dir");
  const dir = at >= 0 ? argv[at + 1] : join(here, "demo-repo");
  createDemoRepo(dir).then((repo) => {
    console.log("Next:");
    console.log(`  npm start                       the gate, in another terminal`);
    console.log(`  npm run agent                   the built-in agent takes tasks.csv through the gate`);
    console.log(`  cd ${repo} && git log --show-notes=chap`);
  }).catch((e) => { console.error(e.message); process.exit(1); });
}
