// The review loop as the commands run it for an agent: the context note it
// gives the reviewer, the review brought to the reviewer's screen, the wait
// for a decision within the time an agent's tool call is allowed, and the
// decision turned into a prompt the agent acts on.

import { readFile } from "node:fs/promises";
import { followUpPrompt } from "../desk/followup.js";
import { api, showReview, waitForDecision } from "./gate.mjs";

/** The most text an agent's context note may be. */
export const MAX_CONTEXT_CHARS = 20_000;

/** The context note from a file, or from standard input with "-". */
export async function readContext(source) {
  if (!source) return null;
  let text;
  if (source === "-") {
    const chunks = [];
    for await (const c of process.stdin) chunks.push(c);
    text = Buffer.concat(chunks).toString("utf8");
  } else {
    text = await readFile(source, "utf8");
  }
  text = text.replace(/\s+$/, "");
  if (!text) return null;
  if (text.length > MAX_CONTEXT_CHARS) throw new Error(`The context note is ${text.length} characters; the gate takes ${MAX_CONTEXT_CHARS}. Keep it to what the reviewer needs.`);
  return text;
}

/** The command line that ran, as a shell can run it again. */
export function commandAgain(script, argv) {
  const q = (s) => (/^[A-Za-z0-9_./:=@%+,-]+$/.test(s) ? s : `"${s.replace(/(["\\$`])/g, "\\$1")}"`);
  return ["node", q(script), ...argv.map(q)].join(" ");
}

/** Bring the review to the reviewer, and say how. */
export async function announce(gate, taskId, { open = true, log = () => {} } = {}) {
  const shown = await showReview(gate, taskId, { open });
  if (shown.how === "desk") log(`The desk is open; the review is waiting there: ${shown.url}`);
  else if (shown.how === "browser") log(`Opened the review in the browser: ${shown.url}`);
  else log(`Review it at ${shown.url}`);
  return shown;
}

/**
 * Wait for the decision, for at most `timeoutMinutes` when given. Returns
 * the task's view, or null when the time ran out with the review still open.
 */
export async function waitWithin(gate, taskId, { pollMs = 2000, timeoutMinutes = null } = {}) {
  try {
    return await waitForDecision(gate, taskId, { pollMs, timeoutMs: timeoutMinutes ? timeoutMinutes * 60_000 : null });
  } catch (e) {
    if (timeoutMinutes && /^No decision on /.test(e.message)) return null;
    throw e;
  }
}

/** How the configuration names a reviewer: their name, else their URI. */
export function reviewerName(gate, uri) {
  if (!uri) return "a reviewer";
  return (gate.config.humans ?? []).find((h) => h.uri === uri)?.display_name ?? uri;
}

/** A line that ends as a sentence: the start, then the reviewer's note when there is one. */
export function withNote(start, note) {
  const text = typeof note === "string" ? note.trim() : "";
  if (!text) return `${start}.`;
  return `${start}: ${text}${/[.!?]$/.test(text) ? "" : "."}`;
}

/**
 * The prompt the last decision on a task makes for the agent, from the
 * task's whole view: the reviewer's note and line comments, and for an
 * edit the difference between the agent's version and the reviewer's.
 */
export async function promptAfter(gate, taskId, command) {
  const view = await api(gate, `/api/tasks/${encodeURIComponent(taskId)}`);
  const decision = view?.decision_log?.at(-1) ?? null;
  if (!view || !decision) return null;
  return followUpPrompt({ task: view, decision, reviewer: reviewerName(gate, decision.reviewer), command });
}

/** Print a prompt for the agent between two rules, so it reads as one piece. */
export function printPrompt(prompt, log) {
  if (!prompt) return;
  log("");
  log("---- The review, for the agent to act on ----");
  log(prompt);
  log("---- end ----");
}
