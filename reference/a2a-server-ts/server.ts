/**
 * CHAP A2A reference server (TypeScript, HTTP/JSON-RPC).
 *
 * Wraps a CHAP Coordinator and serves it as an A2A 0.3.0 agent over
 * Express. Other A2A-aware orchestrators can register this agent by
 * its base URL, discover capabilities at the agent-card endpoint,
 * and delegate work to it via JSON-RPC ``message/send``.
 *
 * Usage:
 *
 *   tsx reference/a2a-server-ts/server.ts [--port 9090]
 *
 * The coordinator runs in-memory in this process. State is lost when
 * the process exits.
 *
 * Spec target: A2A 0.3.0 (the version implemented by @a2a-js/sdk).
 * CHAP 0.2.
 */

import express from "express";
import {
  DefaultRequestHandler,
  InMemoryTaskStore,
} from "@a2a-js/sdk/server";
import { A2AExpressApp } from "@a2a-js/sdk/server/express";

import { Coordinator } from "@brightbeamai/coordinator";
import {
  makeChapAgentCard,
  makeChapAgentExecutor,
} from "@brightbeamai/coordinator-a2a";

function parsePort(): number {
  const idx = process.argv.indexOf("--port");
  if (idx >= 0 && process.argv[idx + 1]) {
    const n = parseInt(process.argv[idx + 1], 10);
    if (!Number.isNaN(n)) return n;
  }
  return 9090;
}

const port = parsePort();
const baseUrl = `http://localhost:${port}`;

const coord = new Coordinator({
  defaultProfiles: [
    "core/1.0", "review/1.0", "whisper/1.0",
    "deliberation/1.0", "handoff/1.0", "control/1.0",
    "routing/1.0", "audit-scitt/1.0",
  ],
});

const card = makeChapAgentCard({
  baseUrl,
  name: "CHAP Coordinator",
  version: "0.2.5",
});

const executor = makeChapAgentExecutor(coord);
const taskStore = new InMemoryTaskStore();

const handler = new DefaultRequestHandler(card, taskStore, executor);
const app = new A2AExpressApp(handler).setupRoutes(express(), "");

process.stderr.write(`CHAP A2A reference server starting on ${baseUrl}\n`);
process.stderr.write(`Agent Card: ${baseUrl}/.well-known/agent-card.json\n`);
process.stderr.write(
  "Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.\n",
);

app.listen(port);
