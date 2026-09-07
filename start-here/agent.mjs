// The same review boundary from Node, with no dependencies.
//
//   1. python start-here/start.py     (leave it running)
//   2. node start-here/agent.mjs
//
// It proposes a draft over the agent capability, waits for you to decide in the
// browser, and prints the object your code would then act on.

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const connectionPath = fileURLToPath(new URL("./.data/agent.json", import.meta.url));

async function connect() {
  try {
    return JSON.parse(await readFile(connectionPath, "utf8"));
  } catch {
    throw new Error("No agent connection file. Start the review desk first:\n"
                  + "  python start-here/start.py");
  }
}

async function call({ base_url, agent_token }, path, options = {}) {
  const response = await fetch(base_url + path, {
    ...options,
    headers: {
      "X-CHAP-Agent": agent_token,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  const body = await response.json().catch(() => ({ error: response.statusText }));
  return { status: response.status, body };
}

/**
 * Wait for a human decision, then return the reviewed object.
 *
 * Every exit from this function says exactly what happened. A timeout says the
 * task is still pending and never claims an approval, and it reaches that line
 * only after a check that found the task still pending.
 */
async function waitForDecision(connection, taskId, { timeoutMs = 300_000, everyMs = 1500 } = {}) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const { status, body } = await call(connection, `/api/tasks/${taskId}/result`);
    if (status === 200) return body.output;
    if (status === 409 && /rejected/i.test(body.error || "")) {
      throw new Error("The human rejected this. Do not run the next step.");
    }
    if (status !== 409) throw new Error(body.error || `HTTP ${status}`);
    if (Date.now() >= deadline) {
      throw new Error("Timed out waiting for a human. The task is still pending "
                    + "and no decision was recorded.");
    }
    await new Promise(resolve => setTimeout(resolve, everyMs));
  }
}

const connection = await connect();
const created = await call(connection, "/api/proposals", {
  method: "POST",
  body: JSON.stringify({
    kind: "draft_response",
    context: { customer: "Where is my order?", tracking: "In transit, no date" },
    draft: { text: "Your order is guaranteed to arrive tomorrow." },
  }),
});
if (created.status !== 201) throw new Error(created.body.error || `HTTP ${created.status}`);

const taskId = created.body.task_id;
console.log("Proposed", taskId, "\nDecide it in the review desk. Waiting.");

try {
  console.log("\nReviewed output:", await waitForDecision(connection, taskId));
} catch (error) {
  console.log("\nStopped:", error.message);
  process.exitCode = 1;
}
