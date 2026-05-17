/**
 * CHAP Core reference client — demo walkthrough.
 *
 * Walks every Core method against a running server:
 *   1. workspace.describe (empty)
 *   2. participant.join × 2 (alice the human, triage-bot the agent)
 *   3. task.create     (alice asks triage-bot for a draft)
 *   4. task.update     (triage-bot moves to in_progress)
 *   5. task.complete   (triage-bot delivers output)
 *   6. audit.read      (alice reads the full log)
 *   7. participant.leave
 *
 * Run:  npm run demo:client   (with the server running)
 */

const CHAP = process.env.CHAP_URL ?? "http://localhost:8080/chap";

interface Envelope {
  jsonrpc: "2.0";
  id?: string;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

let counter = 0;
function id(): string {
  return `demo-${(++counter).toString().padStart(4, "0")}`;
}

async function call(method: string, params: Record<string, unknown>): Promise<unknown> {
  const env: Envelope = { jsonrpc: "2.0", id: id(), method, params };
  const res = await fetch(CHAP, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify(env),
  });
  const body = (await res.json()) as Envelope;
  console.log(`\n→ ${method}`);
  console.log(`  params: ${JSON.stringify(params).slice(0, 80)}…`);
  if (body.error) {
    console.error(`  ✗ error: ${body.error.code} ${body.error.message}`);
    throw new Error(`${method} failed`);
  }
  console.log(`  ✓ result: ${JSON.stringify(body.result).slice(0, 120)}`);
  return body.result;
}

async function main(): Promise<void> {
  const WS = "wsp_demo";
  const ALICE = "human:alice@example.org";
  const BOT = "agent:triage-bot";
  const COORD = "service:coordinator@example.org";

  console.log(`CHAP Core demo against ${CHAP}`);
  console.log("=".repeat(60));

  // 1. participant.join — alice
  await call("participant.join", {
    workspace:    WS,
    from:         ALICE,
    to:           COORD,
    ts:           new Date().toISOString(),
    type:         "human",
    display_name: "Alice",
    role:         "reviewer",
  });

  // 2. participant.join — triage-bot
  await call("participant.join", {
    workspace:    WS,
    from:         BOT,
    to:           COORD,
    ts:           new Date().toISOString(),
    type:         "agent",
    display_name: "Triage Bot v0.1",
    role:         "drafter",
    capabilities: { kinds: ["draft_response"] },
  });

  // 3. workspace.describe — see who's in
  await call("workspace.describe", {
    workspace: WS,
    from:      ALICE,
    to:        COORD,
    ts:        new Date().toISOString(),
  });

  // 4. task.create — alice delegates to the bot
  const created = (await call("task.create", {
    workspace: WS,
    from:      ALICE,
    to:        BOT,
    ts:        new Date().toISOString(),
    kind:      "draft_response",
    assignee:  BOT,
    input:     { ticket_id: "INC-48219", customer_message: "Where's my order?" },
  })) as { task_id: string };
  const TASK = created.task_id;

  // 5. task.update — bot starts work
  await call("task.update", {
    workspace:     WS,
    from:          BOT,
    to:            ALICE,
    ts:            new Date().toISOString(),
    task_id:       TASK,
    state:         "in_progress",
    progress_note: "Looking up order status.",
  });

  // 6. task.complete — bot delivers
  await call("task.complete", {
    workspace: WS,
    from:      BOT,
    to:        ALICE,
    ts:        new Date().toISOString(),
    task_id:   TASK,
    output: {
      subject: "Re: order status",
      body:    "Hi — your order ORD-91204 is delayed by the carrier; new ETA Wed.",
    },
    confidence: 0.91,
  });

  // 7. audit.read — alice reads what happened
  await call("audit.read", {
    workspace: WS,
    from:      ALICE,
    to:        COORD,
    ts:        new Date().toISOString(),
    range:     { from_seq: 0, to_seq: 100 },
  });

  // 8. participant.leave
  await call("participant.leave", {
    workspace: WS,
    from:      BOT,
    to:        COORD,
    ts:        new Date().toISOString(),
    reason:    "demo_complete",
  });

  console.log("\n" + "=".repeat(60));
  console.log("Demo complete. All 7 Core methods exercised against the server.");
}

main().catch((e) => {
  console.error("Demo failed:", e);
  process.exit(1);
});
