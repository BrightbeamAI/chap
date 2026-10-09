/**
 * The triage agent. It participates in the CHAP workspace as
 * `agent:triage-bot@local` and drafts responses to support tickets with
 * the model the environment names (see providers.ts), or with a scripted
 * drafter when none is named.
 *
 * The agent calls the Coordinator the same way any other participant
 * would: by constructing JSON-RPC envelopes and submitting them.
 * There is no privileged in-process API; this is the real protocol.
 */

import type { Coordinator, Envelope, ArtefactRoutingHints } from "@brightbeamai/chap-coordinator";
import type { Ticket } from "./tickets.js";
import { makeProvider, type Provider } from "./providers.js";

// CHAP_NO_LLM=1 is the older switch for the scripted drafter and still works.
if (process.env.CHAP_NO_LLM === "1" && !process.env.CHAP_MODEL_PROVIDER) {
  process.env.CHAP_MODEL_PROVIDER = "scripted";
}

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
 * Parse the model's output. We expect a JSON object on a single line,
 * but a model sometimes adds markdown fences or commentary, so the
 * first object in the text is taken.
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

let provider: Provider | undefined;

/** The provider chosen from the environment, built once. */
export function getProvider(): Provider {
  if (!provider) provider = makeProvider(scriptedDraft);
  return provider;
}

export async function draftResponse(ticket: Ticket): Promise<DraftResult> {
  const prompt = DRAFT_PROMPT
    .replace("__SUBJECT__", ticket.subject)
    .replace("__BODY__",    ticket.body);

  const { text, latency_ms } = await getProvider().complete(prompt);
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
 * The scripted drafter. It reads the ticket subject out of the prompt and
 * answers in the shape the prompt asks for, so the playground runs with no
 * model installed and the routing policy still sees varied signals.
 */
function scriptedDraft(prompt: string): string {
  const m = /Ticket subject: (.*)\n/.exec(prompt);
  const s = (m?.[1] ?? "").toLowerCase();
  let body: string, tone: string, severity: string, self_confidence: number;
  if (s.includes("refund") || s.includes("money back")) {
    body = "Sorry to hear that. We'll review the order and process a refund within 3 business days.";
    tone = "apologetic"; severity = "medium"; self_confidence = 0.8;
  } else if (s.includes("broken") || s.includes("damaged") || s.includes("not working")) {
    body = "Apologies for the trouble. Could you share a photo and your order number? We'll arrange a replacement at no charge.";
    tone = "apologetic"; severity = "high"; self_confidence = 0.7;
  } else if (s.includes("cancel") || s.includes("urgent")) {
    body = "We've put the request on the queue. Confirming details shortly.";
    tone = "formal"; severity = "critical"; self_confidence = 0.4;
  } else if (s.includes("track") || s.includes("where")) {
    body = "Your order is on the way. Tracking links go out the day after dispatch; let us know if you don't see one.";
    tone = "warm_professional"; severity = "low"; self_confidence = 0.9;
  } else {
    body = "Thanks for reaching out. We've recorded your message and a human will follow up.";
    tone = "warm_professional"; severity = "low"; self_confidence = 0.5;
  }
  return JSON.stringify({ body, tone, severity, self_confidence });
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

  // 3. Draft with the provider.
  const draft = await drafter(ticket);

  // 4. Submit completion with routing_hints (the measurement signals).
  // Confidence is a decimal, so it is carried as a string to satisfy the
  // canonical-number rule (decimals must be strings for deterministic
  // hashing); the routing policy coerces it back to a number.
  const confidenceStr = String(draft.self_confidence);
  const artefactHints: ArtefactRoutingHints = {
    confidence:       confidenceStr,
    model_id:         getProvider().model_id,
    cost_consumed_usd: 0,           // the providers do not report cost
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
      confidence:       confidenceStr,
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
    confidence: confidenceStr,
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
 * Process every ticket through the bot, one at a time so a local model is
 * given one request at a time. Used at workspace bootstrap.
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
 * Probe the provider. Used at server startup and by /api/health, so the
 * status bar can say which model is drafting, or that none is.
 */
export async function probeProvider(): Promise<{ ok: boolean; detail: string; provider: string; model: string }> {
  const p = getProvider();
  const probe = await p.probe();
  return { ...probe, provider: p.name, model: p.model_id };
}

/** Kept for callers of the older name. */
export const probeOllama = probeProvider;
