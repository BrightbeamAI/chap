/**
 * Ollama agent. participates in the CHAP workspace as
 * `agent:triage-bot@local`. Drafts responses to support tickets
 * using a local Gemma3 model via Ollama's HTTP API.
 *
 * The agent calls the Coordinator the same way any other participant
 * would: by constructing JSON-RPC envelopes and submitting them.
 * There is no privileged in-process API; this is the real protocol.
 */

import type { Coordinator, Envelope, ArtefactRoutingHints } from "@brightbeamai/coordinator";
import type { Ticket } from "./tickets.js";

const OLLAMA_URL   = process.env.OLLAMA_URL   ?? "http://localhost:11434";
const OLLAMA_MODEL = process.env.OLLAMA_MODEL ?? "gemma3:4b";
const CHAP_NO_LLM  = process.env.CHAP_NO_LLM === "1";

export const BOT_URI = "agent:triage-bot@local";

const DRAFT_PROMPT = `You are a customer-support drafter for an
online retailer. You are NOT the final responder. a human will review
your draft. Read the ticket and produce a short response.

Return ONLY a JSON object on a single line, no commentary, no
markdown fences, in this exact shape:
{"body":"<your response, 1-3 sentences>","tone":"warm_professional|apologetic|formal","severity":"low|medium|high|critical","self_confidence":<number 0..1>}

Keep "body" brief. Tone should match the situation. don't apologise
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
  if (CHAP_NO_LLM) {
    return mockDraft(ticket);
  }
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
 * Deterministic mock drafter. Used when CHAP_NO_LLM=1. Produces a
 * plausible-looking draft based on the ticket subject, so the
 * playground can demonstrate the review/override flow without
 * requiring Ollama or a model download. Tone, severity, and
 * self_confidence vary by ticket so the routing policy still has
 * something interesting to do.
 */
function mockDraft(ticket: Ticket): DraftResult {
  const s = ticket.subject.toLowerCase();
  const t0 = Date.now();
  // Pseudo-deterministic latency derived from subject length so the
  // routing policy sees a stable signal across reruns.
  const latency_ms = 120 + (ticket.subject.length % 40);

  let body:     string;
  let tone:     string;
  let severity: string;
  let self_confidence: number;

  if (s.includes("refund") || s.includes("money back")) {
    body = "Sorry to hear that. We'll review the order and process a refund within 3 business days.";
    tone = "apologetic";
    severity = "medium";
    self_confidence = 0.8;
  } else if (s.includes("broken") || s.includes("damaged") || s.includes("not working")) {
    body = "Apologies for the trouble. Could you share a photo and your order number? We'll arrange a replacement at no charge.";
    tone = "apologetic";
    severity = "high";
    self_confidence = 0.7;
  } else if (s.includes("cancel") || s.includes("urgent")) {
    body = "We've put the request on the queue. Confirming details shortly.";
    tone = "formal";
    severity = "critical";
    self_confidence = 0.4;
  } else if (s.includes("track") || s.includes("where")) {
    body = "Your order is on the way. Tracking links go out the day after dispatch; let us know if you don't see one.";
    tone = "warm_professional";
    severity = "low";
    self_confidence = 0.9;
  } else {
    body = "Thanks for reaching out. We've recorded your message and a human will follow up.";
    tone = "warm_professional";
    severity = "low";
    self_confidence = 0.5;
  }
  return {
    body, tone, severity, self_confidence,
    raw_response: `(CHAP_NO_LLM=1 mock for: ${ticket.subject})`,
    latency_ms:   Date.now() - t0 + latency_ms,
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
      workspace: workspaceId,
      from:         "service:coord@local",
      kind:         "draft_response",
      assignee:     BOT_URI,
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
  if (!createRes.result) {
    throw new Error(`task.create failed: ${JSON.stringify(createRes.error)}`);
  }
  const taskId = (createRes.result as { task_id: string }).task_id;

  // 2. Bot reports in_progress.
  coord.dispatch({
    jsonrpc: "2.0", id: `ev-${Date.now()}-2`, method: "task.update",
    params: { workspace: workspaceId, task_id: taskId, from: BOT_URI, state: "in_progress" },
  });

  // 3. Draft via Ollama.
  const draft = await drafter(ticket);

  // 4. Submit completion with routing_hints (the measurement signals).
  const artefactHints: ArtefactRoutingHints = {
    confidence:       draft.self_confidence,
    model_id:         OLLAMA_MODEL,
    cost_consumed_usd: 0,           // local model. no API cost
    latency_ms:       draft.latency_ms,
  };

  coord.dispatch({
    jsonrpc: "2.0", id: `ev-${Date.now()}-3`, method: "task.complete",
    params: {
      workspace:     workspaceId,
      task_id:          taskId,
      from:             BOT_URI,
      output: {
        body:     draft.body,
        tone:     draft.tone,
        severity: draft.severity,
      },
      confidence:       draft.self_confidence,
      routing_hints:    artefactHints,
    },
  });

  // 5. Routing decisions. The library's routing/1.0 handlers produce
  // route_decision artefacts and inform the reviewer set; the agent
  // assembles the final review.request envelope from their results.
  // (Older library versions folded this into task.complete; the
  // spec-correct shape is explicit method calls.)
  //
  // First, fold the artefact's confidence onto the task's hints so
  // review.depth and escalate.auto can see it. The task carries the
  // task's own hints; the per-artefact hints (confidence, etc.) are
  // passed in alongside.
  const taskHintsForRouting: Record<string, unknown> = {
    ...(ticket.routing_hints as Record<string, unknown>),
    confidence: draft.self_confidence,
  };
  // Stash the merged hints onto the task so the routing handlers
  // (which read from the task) see them. This is a small convenience
  // of the in-process Coordinator; over the wire, the agent would
  // pass artefact_routing_hints in the call.
  {
    const ws = coord.getWorkspace(workspaceId);
    const t = ws?.tasks.get(taskId);
    if (t) t.routing_hints = taskHintsForRouting as never;
  }

  coord.dispatch({
    jsonrpc: "2.0", id: `ev-${Date.now()}-4`, method: "review.depth",
    params: { workspace: workspaceId, task_id: taskId, from: BOT_URI },
  });

  const escRes = coord.dispatch({
    jsonrpc: "2.0", id: `ev-${Date.now()}-5`, method: "escalate.auto",
    params: { workspace: workspaceId, task_id: taskId, from: BOT_URI },
  });
  const escalated = !!(escRes.result as { escalate?: boolean } | undefined)?.escalate;
  const escalateTo = (escRes.result as { to?: string } | undefined)?.to;

  // 6. Assemble the reviewer set. Maya is the default reviewer; if
  // routing escalated, add the senior pool (Sam) too.
  const reviewers: string[] = [reviewer];
  if (escalated && escalateTo && !reviewers.includes(escalateTo)) {
    reviewers.push(escalateTo);
  }

  // 7. Open the review.
  coord.dispatch({
    jsonrpc: "2.0", id: `ev-${Date.now()}-6`, method: "review.request",
    params: {
      workspace: workspaceId,
      task_id:   taskId,
      from:      BOT_URI,
      to:        reviewers,
      rule:      "any_one_approves",
      artefact: {
        body:     draft.body,
        tone:     draft.tone,
        severity: draft.severity,
      },
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
