// The built-in coding agent: `node agent.mjs [tasks.csv] [options]`.
//
//   --repo <path>     the repository to work in (default: ./demo-repo)
//   --branch <name>   the branch to commit on (default: agent/<date>), made from HEAD when missing
//   --once            take the rows in the file through the gate, then exit
//   --poll <ms>       how often to look for a decision (default 2000)
//
// Each row of the CSV (columns title, brief and an optional files hint) is
// one task. The agent drafts the change with the model the environment
// names, or with a scripted drafter when none is set, writes it into the
// working tree, proposes it as a patch, waits for the decision at the
// desk, and commits an approved change through the gate's hooks, so the
// commit carries the trailers and the evidence note. An override is
// applied before the commit, so what lands is what the reviewer approved.
// A rejection that asks for a revision has the agent draft again with the
// reviewer's note. A rejection takes the change back out of the working
// tree. A restarted agent finds its tasks by their idempotency keys and
// commits what was approved and not yet committed.
//
// The agent works one task at a time on one branch, since a working tree
// holds one change at a time. Claude Code, Cursor and other agents work in
// the repository themselves and use propose.mjs; this one shows the whole
// path with nothing else installed.

import { readFile, mkdir, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { makeProvider } from "./lib/providers.mjs";
import { agentClient, describeChange, lastDecision, loadGate, propose, reviewerPresent, sha256, task as readTask, waitForDecision } from "./lib/gate.mjs";
import { applyToWorkingTree, commitAll, commitsWithTrailer, currentBranch, git, head, patchApplies, repoRoot, workingTreePatch } from "./lib/git.mjs";
import { syncOverride } from "./propose.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const MAX_CONTEXT_BYTES = 60_000;

// -- tasks.csv ----------------------------------------------------------------

/** RFC 4180 rows as objects keyed by the header line. */
export function parseCsv(text) {
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false; } else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((f) => f !== "")) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field);
  if (row.some((f) => f !== "")) rows.push(row);
  const [header, ...body] = rows;
  if (!header) return [];
  return body.map((r) => Object.fromEntries(header.map((h, i) => [h.trim(), (r[i] ?? "").trim()])));
}

export async function readTasks(path) {
  return parseCsv(await readFile(path, "utf8")).filter((t) => t.title && t.brief).map((t) => ({
    title: t.title, brief: t.brief, files: (t.files ?? "").split(";").map((s) => s.trim()).filter(Boolean),
    key: "task-" + sha256(`${t.title}\n${t.brief}`).slice(0, 16),
  }));
}

// -- the prompt and what comes back ------------------------------------------

/** The files to show the model: the hinted ones, then the ones whose path matches a word of the task, within a byte budget. */
export async function contextFiles(repo, task) {
  const all = (await git(repo, ["ls-files"])).split("\n").filter(Boolean);
  const words = `${task.title} ${task.brief}`.toLowerCase().match(/[a-z][a-z0-9_.-]{2,}/g) ?? [];
  const score = (path) => (task.files.includes(path) ? 100 : 0) + words.filter((w) => path.toLowerCase().includes(w)).length;
  const ranked = all.map((p) => [p, score(p)]).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const out = [];
  let used = 0;
  for (const [path] of ranked) {
    let content;
    try { content = await readFile(join(repo, path), "utf8"); } catch { continue; }
    if (content.includes("\u0000")) continue;
    if (used + content.length > MAX_CONTEXT_BYTES) continue;
    used += content.length;
    out.push({ path, content });
  }
  return { all, shown: out };
}

export function buildPrompt(task, context, { revision = null, previousPatch = null } = {}) {
  const lines = [
    "You are a coding agent working in a git repository. Make exactly the change the task asks for, and nothing else.",
    `Task: ${task.title}`,
    `Brief: ${task.brief}`,
    "",
    "Repository files:",
    ...context.all.map((p) => `  ${p}`),
    "",
    "Current contents of the files most likely involved:",
  ];
  for (const f of context.shown) lines.push(`--- ${f.path} ---`, f.content, `--- end of ${f.path} ---`);
  if (revision) {
    lines.push("", `Revision requested by the reviewer: ${revision}`);
    if (previousPatch) lines.push("The previous attempt, as a diff:", previousPatch);
  }
  lines.push(
    "",
    "Answer with JSON only, no prose and no code fences:",
    '{"summary": "one line on what changed", "files": [{"path": "relative/path", "content": "the whole new content of the file"}], "delete": ["relative/path"]}',
    "Include a file only when its content changes. Keep every other file as it is. Tests live beside the code they test.",
  );
  return lines.join("\n");
}

/** The JSON object in a model's answer, with or without code fences. */
export function parseAnswer(text) {
  const trimmed = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start < 0 || end < 0) throw new Error("the answer holds no JSON object");
  const answer = JSON.parse(trimmed.slice(start, end + 1));
  if (!Array.isArray(answer.files)) throw new Error("the answer has no files list");
  for (const f of answer.files) {
    if (typeof f.path !== "string" || typeof f.content !== "string") throw new Error("each file needs a path and a content string");
    if (f.path.startsWith("/") || f.path.split("/").includes("..")) throw new Error(`a path outside the repository: ${f.path}`);
  }
  return { summary: String(answer.summary ?? ""), files: answer.files, delete: Array.isArray(answer.delete) ? answer.delete.filter((p) => typeof p === "string" && !p.startsWith("/") && !p.split("/").includes("..")) : [] };
}

/** Write the answer into the working tree. */
export async function applyAnswer(repo, answer) {
  for (const f of answer.files) {
    await mkdir(dirname(join(repo, f.path)), { recursive: true });
    await writeFile(join(repo, f.path), f.content);
  }
  for (const p of answer.delete) await rm(join(repo, p), { force: true });
}

// -- the scripted drafter -----------------------------------------------------

const field = (prompt, name) => (prompt.match(new RegExp(`^${name}: (.*)$`, "m")) ?? [])[1] ?? "";
const shownFile = (prompt, path) => {
  const m = prompt.match(new RegExp(`^--- ${path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} ---\\n([\\s\\S]*?)\\n--- end of ${path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} ---$`, "m"));
  return m ? m[1] : null;
};

const VALIDATION = `function assertNumber(value, name) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new TypeError(\`\${name} must be a finite number\`);
  }
}
`;

/**
 * The drafter used when no model is named. It knows the three sample tasks
 * for the demo repository and, for any other task, leaves a note in
 * NOTES.md, so the gate's path can be walked with nothing configured.
 */
export function scriptedDraft(prompt) {
  const title = field(prompt, "Task").toLowerCase();
  const revision = field(prompt, "Revision requested by the reviewer");
  const calc = shownFile(prompt, "lib/calc.mjs") ?? "";
  const tests = shownFile(prompt, "test/calc.test.mjs") ?? "";
  const readme = shownFile(prompt, "README.md") ?? "";
  const stamp = revision ? `// Revision: ${revision}\n` : "";
  const answer = { summary: "", files: [], delete: [] };
  if (title.includes("validat")) {
    const body = calc.includes("assertNumber") ? calc : calc.replace("export function add(a, b) {\n", `${VALIDATION}\nexport function add(a, b) {\n  assertNumber(a, "a");\n  assertNumber(b, "b");\n`);
    answer.summary = "add() refuses anything that is not a finite number";
    answer.files.push({ path: "lib/calc.mjs", content: stamp + body.replace(/^\/\/ Revision: .*\n/, "") });
    if (!tests.includes("rejects")) {
      answer.files.push({ path: "test/calc.test.mjs", content: tests + `
test("add rejects what is not a finite number", () => {
  assert.throws(() => add("2", 3), TypeError);
  assert.throws(() => add(2, Infinity), TypeError);
});
` });
    }
  } else if (title.includes("subtract")) {
    let body = calc;
    if (!body.includes("assertNumber")) body = body.replace("export function add(a, b) {\n", `${VALIDATION}\nexport function add(a, b) {\n`);
    if (!body.includes("export function subtract")) body += `
export function subtract(a, b) {
  assertNumber(a, "a");
  assertNumber(b, "b");
  return a - b;
}
`;
    answer.summary = "subtract(a, b) with the same validation as add";
    answer.files.push({ path: "lib/calc.mjs", content: stamp + body.replace(/^\/\/ Revision: .*\n/, "") });
    if (!tests.includes("subtract")) {
      answer.files.push({ path: "test/calc.test.mjs", content: tests.replace('import { add, multiply } from "../lib/calc.mjs";', 'import { add, multiply, subtract } from "../lib/calc.mjs";') + `
test("subtract subtracts", () => {
  assert.equal(subtract(5, 3), 2);
});
` });
    }
    if (!readme.includes("subtract")) answer.files.push({ path: "README.md", content: readme.replace("- `multiply(a, b)`\n", "- `multiply(a, b)`\n- `subtract(a, b)`\n") });
  } else if (title.includes("document")) {
    const names = [...calc.matchAll(/^export function (\w+)\(/gm)].map((m) => m[1]);
    const examples = names.map((n) => `### \`${n}(a, b)\`\n\n\`\`\`js\nimport { ${n} } from "./lib/calc.mjs";\n${n}(6, 3);\n\`\`\`\n`).join("\n");
    answer.summary = "README shows how to install and use every function";
    answer.files.push({ path: "README.md", content: `# calc\n\nA small calculator library used to show the CHAP code gate.\n${revision ? `\n<!-- Revision: ${revision} -->\n` : ""}\n## Install\n\n\`\`\`bash\nnpm install\nnpm test\n\`\`\`\n\n## Use\n\n${examples}` });
  } else {
    answer.summary = `Note on: ${field(prompt, "Task")}`;
    const notes = shownFile(prompt, "NOTES.md") ?? "# Notes\n";
    answer.files.push({ path: "NOTES.md", content: `${notes}\n- ${field(prompt, "Task")}: ${field(prompt, "Brief")}${revision ? ` (revision: ${revision})` : ""}\n` });
  }
  return JSON.stringify(answer);
}

// -- the loop -------------------------------------------------------------------

/** Draft the task into the working tree and return the artefact, or null when nothing changed. */
async function draft(repo, provider, task, { revision = null, previousPatch = null, log } = {}) {
  const context = await contextFiles(repo, task);
  const { text } = await provider.complete(buildPrompt(task, context, { revision, previousPatch }));
  let answer;
  try { answer = parseAnswer(text); } catch (e) { log(`${task.key}: the model's answer could not be used (${e.message})`); return null; }
  await applyAnswer(repo, answer);
  return describeChange(repo, { summary: task.title, drafted_by: provider.model_id, requested_by: "tasks.csv" });
}

/** Take back whatever the working tree holds beyond HEAD. */
async function discardWorkingTree(repo) {
  const patch = await workingTreePatch(repo);
  if (patch.trim() && (await patchApplies(repo, patch, { reverse: true }))) await applyToWorkingTree(repo, patch, { reverse: true });
}

/**
 * One task, start to finish: its outcome is approve, override, reject, or
 * the state the task was left in. `item` keeps the attempt count.
 */
export async function handleTask({ gate, client, provider, repo, task, log, pollMs }) {
  const base = await head(repo);
  const branch = await currentBranch(repo);
  const created = await client.call("task.create", {
    kind: "code_change", assignee: client.from, review_required: true, idempotency_key: task.key,
    input: { summary: task.title, brief: task.brief, repo: repo.split("/").pop(), branch, base, files: task.files, requested_by: "tasks.csv" },
  });
  const id = created.task_id;
  let view = await readTask(gate, id);
  let attempts = 0;
  for (;;) {
    const decision = lastDecision(view);
    if (view.state === "completed") {
      const committed = await commitsWithTrailer(repo, "CHAP-Task", id);
      if (committed.length) { log(`${task.key}: committed earlier as ${committed[0].slice(0, 12)}`); return decision?.kind ?? "approve"; }
      await discardWorkingTree(repo);
      if (!(await patchApplies(repo, view.output.patch))) { log(`${task.key}: the approved patch no longer applies to ${branch}; nothing committed`); return "stale"; }
      await applyToWorkingTree(repo, view.output.patch);
      const sha = await commitAll(repo, `${view.output.summary ?? task.title}\n`);
      log(`${task.key}: ${decision?.kind === "override" ? "approved with an edit" : "approved"} by ${decision?.reviewer}; committed as ${sha.slice(0, 12)} on ${branch}`);
      return decision?.kind ?? "approve";
    }
    if (view.state === "declined") {
      await discardWorkingTree(repo);
      log(`${task.key}: rejected by ${decision?.reviewer}${decision?.comment ? `: ${decision.comment}` : ""}; nothing committed`);
      return "reject";
    }
    if (view.state === "review_requested") {
      log(`${task.key}: waiting for the decision at ${gate.base}/`);
      view = await waitForDecision(gate, id, { pollMs });
      continue;
    }
    if (view.state !== "created" && view.state !== "in_progress") { log(`${task.key}: the task is ${view.state}; nothing more to do`); return view.state; }
    // created, or in_progress after a rejection that asked for a revision
    const revision = view.state === "in_progress" && decision?.kind === "reject" ? decision.comment ?? "no note" : null;
    const previousPatch = revision ? await workingTreePatch(repo) : null;
    if (++attempts > 3) { log(`${task.key}: no usable change after three attempts; the task stays open for a person`); return view.state; }
    const artefact = await draft(repo, provider, task, { revision, previousPatch, log });
    if (!artefact) { log(`${task.key}: the draft changed nothing; trying again`); continue; }
    // The review is addressed to the human members, so until one has joined
    // the submission would be refused and the refusal recorded. The agent
    // waits on workspace.describe, a read, and says so once.
    if (!(await reviewerPresent(client))) {
      log(`${task.key}: no reviewer has joined yet; open the desk at ${gate.base}/ and the draft is submitted then`);
      while (!(await reviewerPresent(client))) await new Promise((r) => setTimeout(r, pollMs));
    }
    const r = await propose(client, artefact, { taskId: id });
    log(`${task.key}: ${revision ? "revised" : "drafted"} with ${provider.model_id}, ${artefact.files.length} file${artefact.files.length === 1 ? "" : "s"}, ${r.state} as ${id}`);
    view = await readTask(gate, id);
  }
}

export async function run({ source, repo: repoPath, branch, once = false, pollMs = 2000, log = console.log, gateDir = here, keyPath } = {}) {
  const gate = await loadGate(gateDir);
  const repo = await repoRoot(repoPath);
  if (!repo) throw new Error(`${repoPath} is not inside a git repository. Make one with: npm run demo-repo`);
  if ((await workingTreePatch(repo)).trim()) throw new Error(`${repo} has uncommitted changes; the agent needs a clean working tree`);
  const client = await agentClient(gate, { keyPath });
  const provider = makeProvider(scriptedDraft);
  const probe = await provider.probe();
  if (!probe.ok) throw new Error(probe.detail);
  branch ??= `agent/${new Date().toISOString().slice(0, 10)}`;
  try { await git(repo, ["rev-parse", "--verify", "--quiet", `refs/heads/${branch}`]); await git(repo, ["switch", "--quiet", branch]); }
  catch { await git(repo, ["switch", "--quiet", "-c", branch]); }
  log(`agent ${client.from} on ${repo} (${branch}), drafting with ${probe.detail}`);
  const done = new Set();
  const outcomes = {};
  for (;;) {
    for (const task of await readTasks(source)) {
      if (done.has(task.key)) continue;
      done.add(task.key);
      try {
        outcomes[task.key] = await handleTask({ gate, client, provider, repo, task, log, pollMs });
      } catch (e) {
        log(`${task.key}: ${e instanceof Error ? e.message : String(e)}`);
        outcomes[task.key] = "error";
      }
    }
    if (once) return outcomes;
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

export function parseArgs(argv) {
  const out = { source: join(here, "tasks.csv"), repo: join(here, "demo-repo"), branch: undefined, once: false, pollMs: 2000 };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--repo") out.repo = argv[++i];
    else if (a === "--branch") out.branch = argv[++i];
    else if (a === "--poll") out.pollMs = Number(argv[++i]);
    else if (a === "--once") out.once = true;
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}`);
    else out.source = resolve(a);
  }
  return out;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  run(parseArgs(process.argv.slice(2))).catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}
