/**
 * Core + Review demo — walks through the override-capture flow.
 *
 *   1. workspace setup (alice the human, triage-bot the agent)
 *   2. alice creates a draft_response task
 *   3. agent drafts and posts review.request
 *   4. alice overrides with a JSON Patch + rationale + tags
 *   5. audit log read — the override is now structured data
 */

const CHAP = process.env.CHAP_URL ?? "http://localhost:8080/chap";
const WS = "wsp_support_triage";
const ALICE  = "human:alice@example.org";
const BOT    = "agent:triage-bot";
const COORD  = "service:coordinator@example.org";

// CHAP 0.2.1 — durable handle for "the response to ticket INC-48219".
// Same id flows from the agent's first draft through the human's override
// so downstream analytics can see they're two versions of the same
// underlying item, not two separate items. See SPEC §9.2.1.
// Format: lgl_ + 26 chars of Crockford-base32 (same shape as ULIDs).
const RESPONSE_LOGICAL_ID = "lgl_01HZSPPRT0RESPND4821942KB5";

let nextId = 0;
async function call(method: string, params: Record<string, unknown>): Promise<any> {
  const env = { jsonrpc: "2.0", id: `c${++nextId}`, method, params };
  const res = await fetch(CHAP, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(env),
  });
  const body = await res.json() as { result?: any; error?: any };
  console.log(`\n→ ${method}`);
  if (body.error) {
    console.error(`  ERROR ${body.error.code}: ${body.error.message}`);
    throw new Error(body.error.message);
  }
  const r = JSON.stringify(body.result);
  console.log(`  ${r.length > 120 ? r.slice(0, 120) + "…" : r}`);
  return body.result;
}

const ts = () => new Date().toISOString();

async function main(): Promise<void> {
  console.log("CHAP Core+Review demo");
  console.log("=".repeat(50));

  // -------- Set up the workspace --------
  await call("participant.join", {
    workspace: WS, from: ALICE, to: COORD, ts: ts(),
    type: "human", display_name: "Alice (senior support)", role: "reviewer",
  });
  await call("participant.join", {
    workspace: WS, from: BOT, to: COORD, ts: ts(),
    type: "agent", display_name: "Triage Bot v0.1", role: "drafter",
    capabilities: { kinds: ["draft_response"] },
  });

  // -------- Alice delegates a draft to the bot --------
  const { task_id } = await call("task.create", {
    workspace: WS, from: ALICE, to: BOT, ts: ts(),
    kind: "draft_response", assignee: BOT,
    input: {
      ticket_id: "INC-48219",
      customer_message: "I really need to know where my order is. This is the third time I'm asking!",
    },
  });

  // -------- The bot drafts (simulated; in reality the agent does this work) --------
  await call("task.update", {
    workspace: WS, from: BOT, to: ALICE, ts: ts(),
    task_id, state: "in_progress",
    progress_note: "Drafting response with carrier lookup.",
  });

  const draft = {
    subject: "Re: order delivery enquiry",
    body:
      "We sincerely apologise for the extreme delay and the multiple times you've " +
      "had to reach out. Your order is in transit and will arrive within the next " +
      "three business days. We deeply regret any inconvenience caused.",
    tone:    "very_apologetic",
    severity: "high",
  };

  // -------- The bot requests review --------
  await call("review.request", {
    workspace: WS, from: BOT, to: [ALICE], ts: ts(),
    task_id, artefact: draft,
    rule: "any_one_approves",
    summary: "First draft of response to INC-48219.",
  });

  // -------- Alice overrides — softens tone, fixes severity --------
  console.log("\n--- Alice reviews. Tone is over-apologetic for what is just a tracking question. ---");

  const override = await call("decide.override", {
    workspace: WS, from: ALICE, to: COORD, ts: ts(),
    task_id,
    based_on_artefact: draft,
    // CHAP 0.2.1 — Alice introduces a logical_id for "the response to
    // INC-48219" and signals that the underlying intent (respond to
    // the customer, give them transit info) is preserved — she refined
    // the tone, not the decision. In a production deployment the agent
    // would typically assign logical_id when it produces the first
    // draft, and Alice's override would reuse that same id; either
    // path is conformant. See SPEC §9.2.1, §9.4.
    logical_id:       RESPONSE_LOGICAL_ID,
    intent_preserved: true,
    diff: [
      {
        op:    "replace",
        path:  "/body",
        value:
          "Thanks for following up. Your order is in transit and should arrive " +
          "within the next three business days. Let me know if you don't see it by then.",
      },
      { op: "replace", path: "/tone",     value: "warm_professional" },
      { op: "replace", path: "/severity", value: "low" },
    ],
    rationale:
      "Tone was over-apologetic for a routine tracking enquiry. Severity downgraded " +
      "since the order is in normal transit.",
    tags:        ["tone-softened", "severity-downgraded", "length-reduced"],
    policy_refs: ["support-tone-guideline-v2", "severity-rubric-v3"],
  });

  console.log(`\nOverride captured. Artefact id: ${override.override_artefact_id}`);

  // -------- Audit read: now query the structured override data --------
  console.log("\n--- The override is now in the audit log as structured data. ---");

  const audit = await call("audit.read", {
    workspace: WS, from: ALICE, to: COORD, ts: ts(),
    filter: { method: "decide.override" },
  });

  console.log(`\nFound ${audit.entries.length} override entries.`);
  for (const e of audit.entries) {
    const p = e.envelope.params;
    console.log(`  • ${p.from} on ${p.task_id}`);
    console.log(`    tags: ${(p.tags ?? []).join(", ")}`);
    console.log(`    rationale: ${p.rationale}`);
  }

  console.log("\n" + "=".repeat(50));
  console.log("Run `tsx analyze-overrides.ts` to see the learning-data dividend.");
}

main().catch((e) => {
  console.error("Demo failed:", e);
  console.error("Is the server running? `npm run start:demo`");
  process.exit(1);
});
