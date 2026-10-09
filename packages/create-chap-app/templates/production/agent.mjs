// The agent, as its own process with its own key.
//
// It reads chap.config.json and its key file, joins the workspace with its
// public key, then takes each row of messages.csv, opens a trial-mode task,
// drafts the message with the configured provider, submits the draft with
// task.complete and waits for the decision made at the desk. An approved
// message is written to outbox/<task_id>.json. A rejected one is written
// nowhere. Every call after the join is signed with the key.
//
//   CHAP_URL             where the coordinator answers, default http://127.0.0.1:8790/chap
//   CHAP_AGENT_KEY       the key file, default keys/<agent-uri-slug>.jwk.json
//   CHAP_MODEL_PROVIDER  anthropic, openai, ollama or scripted (lib/providers.mjs)
//   --once               handle the rows in messages.csv, wait for their decisions, exit
//
// Without --once the agent keeps running and reads messages.csv again on
// every pass, so a row added to the file becomes a task. Each row carries an
// idempotency key made from its content, so a restarted agent finds the
// tasks it opened before and opens no duplicates. A rejection that asks for
// a revision has the agent draft again with the reviewer's note. Before it
// submits a draft the agent checks on workspace.describe, a read, that a
// reviewer has joined, since a completion with nobody to address the review
// to is refused and the refusal recorded.

import { createHash } from "node:crypto";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { makeClient, signerFromJwk } from "./desk/chap-client.mjs";
import { makeProvider } from "./lib/providers.mjs";
import { keyPathFor, readKeyFile } from "./keys.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const POLL_MS = 2000;

export function chapUrl() {
  return process.env.CHAP_URL ?? "http://127.0.0.1:8790/chap";
}

/** The HTTP base beside POST /chap, where GET /api/... answers. */
export function apiBase(url = chapUrl()) {
  return url.replace(/\/chap\/?$/, "");
}

// -- messages.csv -------------------------------------------------------------

/** RFC 4180 rows as objects keyed by the header line. Quoted fields may hold commas and newlines. */
export function parseCsv(text) {
  const rows = [];
  let row = [], field = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += c;
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

/** The idempotency key for a row: the same row opens the same task on every run. */
export function rowKey(row) {
  return "msg-" + createHash("sha256").update(JSON.stringify([row.to, row.subject, row.brief])).digest("hex").slice(0, 24);
}

// -- drafting -----------------------------------------------------------------

export function buildPrompt(row, revision) {
  const lines = [
    "Write the body of a short, courteous email. Reply with the body only, no subject line.",
    `To: ${row.to}`,
    `Subject: ${row.subject}`,
    `Brief: ${row.brief}`,
  ];
  if (revision) lines.push(`The reviewer asked for a revision: ${revision}`);
  return lines.join("\n");
}

/** The scripted drafter: a deterministic body from the brief in the prompt, no model. */
export function scriptedDraft(prompt) {
  const field = (name) => (prompt.match(new RegExp(`^${name}: (.*)$`, "m")) ?? [])[1] ?? "";
  const to = field("To");
  const name = (to.split("<")[0].trim() || "there").replace(/^(Dr|Mr|Mrs|Ms|Prof)\.?\s+/, "").split(" ")[0];
  const revision = field("The reviewer asked for a revision");
  const brief = field("Brief");
  const body = [`Dear ${name},`, "", brief];
  if (revision) body.push("", `Following your note: ${revision}`);
  body.push("", "Kind regards,", "The drafter");
  return body.join("\n");
}

export async function draft(provider, row, revision) {
  const { text, model_id } = await provider.complete(buildPrompt(row, revision));
  return { to: row.to, subject: row.subject, body: text.trim(), model: model_id };
}

// -- the agent ----------------------------------------------------------------

/** The config, the key and a signing client for the agent. */
export async function loadAgent({ dir = here, url = chapUrl(), keyPath } = {}) {
  const config = JSON.parse(await readFile(join(dir, "chap.config.json"), "utf8"));
  const uri = config.agent?.uri;
  if (!uri) throw new Error("chap.config.json names no agent");
  keyPath ??= process.env.CHAP_AGENT_KEY ?? keyPathFor(uri, join(dir, "keys"));
  const signer = await signerFromJwk(uri, await readKeyFile(uri, keyPath));
  const client = makeClient({ url, workspace: config.workspace, from: uri, signer });
  return { config, uri, signer, client, keyPath };
}

/** Join with the public key. participant.join is accepted unsigned; everything after it is signed. */
export async function joinWorkspace(client, config) {
  return client.call("participant.join", {
    type: "agent",
    role: config.agent.role ?? "drafter",
    display_name: config.agent.display_name,
    jwks: { keys: [client.signer.publicJwk] },
  });
}

async function fetchTask(base, taskId) {
  const res = await fetch(`${base}/api/tasks/${encodeURIComponent(taskId)}`);
  if (!res.ok) throw new Error(`GET /api/tasks/${taskId} answered ${res.status}`);
  return res.json();
}

const lastDecision = (view) => view.review?.decisions?.at(-1) ?? null;

/**
 * Run the workload. Returns when `once` is set and every task opened from
 * messages.csv has been decided; otherwise keeps reading the file.
 */
export async function run({ once = false, dir = here, url = chapUrl(), keyPath, outbox = join(dir, "outbox"), log = console.log, pollMs = POLL_MS } = {}) {
  const { config, uri, client } = await loadAgent({ dir, url, keyPath });
  const base = apiBase(url);
  const provider = makeProvider(scriptedDraft);
  await mkdir(outbox, { recursive: true });
  log(`agent ${uri} calling ${url}, drafting with ${provider.detail}`);

  // A join names a workspace, and the coordinator creates one it does not
  // have, so a mismatch with the desk's configuration would leave the
  // agent's tasks in a workspace the desk never shows.
  const served = await (await fetch(`${base}/api/config`)).json();
  if (served.workspace !== config.workspace) {
    throw new Error(`${base} serves ${served.workspace}, and chap.config.json here names ${config.workspace}`);
  }
  await joinWorkspace(client, config);
  log(`joined ${config.workspace} with key ${client.signer.kid}; every call from here on is signed`);

  const seen = new Set();
  const pending = new Map();
  const outcomes = {};
  for (;;) {
    try {
      const rows = parseCsv(await readFile(join(dir, "messages.csv"), "utf8"));
      for (const row of rows) {
        const key = rowKey(row);
        if (seen.has(key) || !row.to || !row.subject) continue;
        seen.add(key);
        const created = await client.call("task.create", {
          kind: "draft_message", assignee: uri, input: { to: row.to, subject: row.subject, brief: row.brief },
          mode: "trial", review_required: true, idempotency_key: key,
        });
        log(`task ${created.task_id} ${created.state}: "${row.subject}"`);
        pending.set(created.task_id, { row, waiting: false, noReviewer: false, draft: null });
      }
      for (const [taskId, item] of pending) {
        const outcome = await advance({ client, base, provider, outbox, taskId, item, log });
        if (outcome) { outcomes[taskId] = outcome; pending.delete(taskId); }
      }
    } catch (e) {
      log(`error: ${e instanceof Error ? e.message : String(e)}`);
    }
    if (once && pending.size === 0) return outcomes;
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

/**
 * Whether a human other than the agent is a member. A review opened on
 * task.complete is addressed to the human members other than the completer,
 * and the completion is refused when there are none.
 */
async function reviewerPresent(client) {
  const ws = await client.call("workspace.describe", {});
  return (ws.members ?? []).some((m) => m.uri !== client.from && m.type === "human");
}

/**
 * One step for one task. Returns nothing while the task is open, and once it
 * is settled the outcome: approve, override or reject, or the state the task
 * reached another way.
 */
async function advance({ client, base, provider, outbox, taskId, item, log }) {
  const view = await fetchTask(base, taskId);
  const { row } = item;
  switch (view.state) {
    case "created":
    case "in_progress": {
      const previous = lastDecision(view);
      const revision = view.state === "in_progress" && previous?.kind === "reject" ? previous.comment ?? "no comment" : null;
      if (!item.draft) {
        item.draft = await draft(provider, row, revision);
        log(`task ${taskId} drafted${revision ? " again, after the reviewer's note" : ""} with ${provider.model_id}`);
      }
      // The review opens addressed to the human members other than the
      // agent, so until one has joined, task.complete would be refused and
      // each refusal recorded. workspace.describe is a read, so waiting on
      // it leaves nothing on the chain. Said once per task.
      if (!(await reviewerPresent(client))) {
        if (!item.noReviewer) log(`task ${taskId} cannot open its review yet: no reviewer has joined. Open the desk at ${base}/ and the draft is submitted on a later pass.`);
        item.noReviewer = true;
        return null;
      }
      const r = await client.call("task.complete", { task_id: taskId, output: item.draft });
      item.draft = null;
      item.waiting = true;
      item.noReviewer = false;
      log(`task ${taskId} ${r.state}: waiting for a decision at the desk`);
      return null;
    }
    case "review_requested":
      if (!item.waiting) { item.waiting = true; log(`task ${taskId} is under review: waiting for a decision at the desk`); }
      return null;
    case "completed": {
      const file = join(outbox, `${taskId}.json`);
      const decision = lastDecision(view);
      if (await exists(file)) {
        log(`task ${taskId} was decided earlier; ${file} is already written`);
      } else {
        await writeFile(file, JSON.stringify({ task_id: taskId, message: view.output, decision }, null, 2) + "\n");
        log(`task ${taskId} ${decision?.kind === "override" ? "approved with an edit" : "approved"} by ${decision?.reviewer ?? "a reviewer"}; written to ${file}`);
      }
      return decision?.kind ?? "approve";
    }
    case "declined": {
      const decision = lastDecision(view);
      log(`task ${taskId} rejected by ${decision?.reviewer ?? "a reviewer"}${decision?.comment ? `: ${decision.comment}` : ""}; nothing written`);
      return "reject";
    }
    default:
      log(`task ${taskId} is ${view.state}; nothing more to do`);
      return view.state;
  }
}

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  run({ once: process.argv.includes("--once") }).catch((e) => {
    console.error(e instanceof Error ? e.message : String(e));
    process.exit(1);
  });
}
