/**
 * Ollama agent — participates in the CHAP workspace as
 * `agent:triage-bot@local`. Drafts responses to support tickets
 * using a local Gemma3 model via Ollama's HTTP API.
 *
 * The agent calls the Coordinator the same way any other participant
 * would: by constructing JSON-RPC envelopes and submitting them.
 * There is no privileged in-process API; this is the real protocol.
 */

import type { Coordinator, Envelope, ArtefactRoutingHints } from "@chap/coordinator";
import type { Ticket } from "./tickets.js";

const OLLAMA_URL   = process.env.OLLAMA_URL   ?? "http://localhost:11434";
const OLLAMA_MODEL = process.env.OLLAMA_MODEL ?? "gemma3:4b";

export const BOT_URI = "agent:triage-bot@local";

const DRAFT_PROMPT = `You are a customer-support drafter for an
online retailer. You are NOT the final responder — a human will review
your draft. Read the ticket and produce a short response.

Return ONLY a JSON object on a single line, no commentary, no
markdown fences, in this exact shape:
{"body":"<your response, 1-3 sentences>","tone":"warm_professional|apologetic|formal","severity":"low|medium|high|critical","self_confidence":<number 0..1>}

Keep "body" brief. Tone should match the situation — don't apologise
for routine requests; be empathetic for serious ones. Self_confidence
should reflect how sure you are this draft is correct: 0.9 for routine
requests where you're sure, 0.5 if you had to guess about policy or
specifics.

Ticket subject: __SUBJECT__
Ticket body:
__BODY__`;

export interface DraftResult {
  body:            string;
  tone:            string;
  severity:        string;
  self_confidence: number;
  raw_response:    string;
  latency_ms:      number;
}

/**
 * Call Ollama. Returns the raw text response.
 */
async function callOllama(prompt: string): Promise<{ text: string; latency_ms: number }> {
  const t0 = Date.now();
  const res = await fetch(`${OLLAMA_URL}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model:  OLLAMA_MODEL,
      prompt,
      stream: false,
      options: { temperature: 0.6, num_predict: 200 },
    }),
  });
  const latency_ms = Date.now() - t0;
  if (!res.ok) {
    throw new Error(`Ollama returned ${res.status}: ${await res.text()}`);
  }
  const data = await res.json() as { response: string };
  return { text: data.response, latency_ms };
}

/**
 * Parse Gemma3's output. We expect a JSON object on a single line,
 * but the model sometimes adds markdown fences or commentary. We try
 * to recover gracefully.
 */
function parseDraft(text: string): Omit<DraftResult, "raw_response" | "latency_ms"> | null {
  // Strip code fences
  const stripped = text.replace(/```(?:json)?/g, "").trim();
  // Find first { and last }
  const start = stripped.indexOf("{");
  const end   = stripped.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  const jsonChunk = stripped.slice(start, end + 1);
  try {
    const obj = JSON.parse(jsonChunk);
    if (typeof obj.body !== "string") return null;
    return {
      body: obj.body,
      tone: typeof obj.tone === "string" ? obj.tone : "warm_professional",
      severity: typeof obj.severity === "string" ? obj.severity : "low",
      self_confidence: typeof obj.self_confidence === "number"
        ? Math.max(0, Math.min(1, obj.self_confidence))
        : 0.5,
    };
  } catch {
    return null;
  }
}

export async function draftResponse(ticket: Ticket): Promise<DraftResult> {
  const prompt = DRAFT_PROMPT
    .replace("__SUBJECT__", ticket.subject)
    .replace("__BODY__",    ticket.body);

  const { text, latency_ms } = await callOllama(prompt);
  const parsed = parseDraft(text);
  if (parsed) {
    return { ...parsed, raw_response: text, latency_ms };
  }
  // Fallback: treat the raw text as the body, low confidence.
  return {
    body: text.trim().slice(0, 600),
    tone: "warm_professional",
    severity: "low",
    self_confidence: 0.3,
    raw_response: text,
    latency_ms,
  };
}

/**
 * Drive a single ticket through the workspace as the bot:
 *   task.create  → task.update(in_progress) → task.complete (review_requested)
 *
 * Returns the task id so callers can listen for the review outcome.
 */
export async function processTicket(
  coord: Coordinator,
  workspaceId: string,
  ticket: Ticket,
  reviewer: string,
  options: { drafter?: (t: Ticket) => Promise<DraftResult> } = {},
): Promise<string> {
  const drafter = options.drafter ?? draftResponse;

  // 1. Create the task addressed to the bot.
  const createEnv: Envelope = {
    jsonrpc: "2.0", id: `ev-${Date.now()}-1`, method: "task.create",
    params: {
      workspace_id: workspaceId,
      kind:         "draft_response",
      assignee:     BOT_URI,
      delegator:    "service:coord@local",
      input: {
        ticket_id: ticket.id,
        subject:   ticket.subject,
        body:      ticket.body,
        customer:  ticket.customer,
      },
      routing_hints: ticket.routing_hints,
    },
  };
  const createRes = coord.dispatch(createEnv);
  const taskId = (createRes.result as { task_id: string }).task_id;

  // 2. Bot reports in_progress.
  coord.dispatch({
    jsonrpc: "2.0", id: `ev-${Date.now()}-2`, method: "task.update",
    params: { workspace_id: workspaceId, task_id: taskId, from: BOT_URI, state: "in_progress" },
  });

  // 3. Draft via Ollama.
  const draft = await drafter(ticket);

  // 4. Submit completion with routing_hints (the measurement signals).
  const artefactHints: ArtefactRoutingHints = {
    confidence:       draft.self_confidence,
    model_id:         OLLAMA_MODEL,
    cost_consumed_usd: 0,           // local model — no API cost
    latency_ms:       draft.latency_ms,
  };

  coord.dispatch({
    jsonrpc: "2.0", id: `ev-${Date.now()}-3`, method: "task.complete",
    params: {
      workspace_id:     workspaceId,
      task_id:          taskId,
      from:             BOT_URI,
      output: {
        body:     draft.body,
        tone:     draft.tone,
        severity: draft.severity,
      },
      confidence:       draft.self_confidence,
      routing_hints:    artefactHints,
      review_requested: true,
      reviewers:        [reviewer],
      rule:             "any_one_approves",
    },
  });

  return taskId;
}

/**
 * Process every ticket through the bot in parallel-ish (serially to
 * avoid hammering the local Ollama). Used at workspace bootstrap.
 */
export async function processAllTickets(
  coord: Coordinator,
  workspaceId: string,
  tickets: Ticket[],
  reviewer: string,
  options: { drafter?: (t: Ticket) => Promise<DraftResult>; onProgress?: (i: number, total: number) => void } = {},
): Promise<string[]> {
  const ids: string[] = [];
  for (let i = 0; i < tickets.length; i++) {
    options.onProgress?.(i, tickets.length);
    const id = await processTicket(coord, workspaceId, tickets[i], reviewer, options);
    ids.push(id);
  }
  options.onProgress?.(tickets.length, tickets.length);
  return ids;
}

/**
 * Probe whether Ollama is reachable. Used at server startup to warn
 * the user if their environment isn't set up.
 */
export async function probeOllama(): Promise<{ ok: boolean; detail: string }> {
  try {
    const res = await fetch(`${OLLAMA_URL}/api/tags`);
    if (!res.ok) return { ok: false, detail: `Ollama at ${OLLAMA_URL} returned ${res.status}` };
    const data = await res.json() as { models?: { name: string }[] };
    const hasModel = data.models?.some((m) => m.name.startsWith(OLLAMA_MODEL.split(":")[0]));
    if (!hasModel) {
      return {
        ok: false,
        detail: `Ollama is reachable but model "${OLLAMA_MODEL}" is not pulled. Run: ollama pull ${OLLAMA_MODEL}`,
      };
    }
    return { ok: true, detail: `Ollama OK with model ${OLLAMA_MODEL}` };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return { ok: false, detail: `Cannot reach Ollama at ${OLLAMA_URL}: ${msg}` };
  }
}
