/**
 * CHAP A2A reference server (TypeScript, HTTP/JSON-RPC).
 *
 * Wraps a CHAP Coordinator and serves it as an A2A 1.0 agent over
 * Express. Other A2A-aware orchestrators can register this agent by
 * its base URL, discover capabilities at the agent-card endpoint,
 * and delegate work to it via JSON-RPC ``SendMessage``. A client still
 * on A2A 0.3 is answered too, ``message/send`` included, through the
 * SDK's compatibility layer.
 *
 * Usage:
 *
 *   tsx reference/a2a-server-ts/server.ts [--port 9090]
 *
 * The coordinator runs in-memory in this process. State is lost when
 * the process exits.
 *
 * Spec target: A2A 1.0, serving 0.3 clients as well. CHAP 0.3.
 */

import express from "express";
import { AGENT_CARD_PATH } from "@a2a-js/sdk";
import {
  DefaultRequestHandler,
  InMemoryTaskStore,
} from "@a2a-js/sdk/server";
import { agentCardHandler, jsonRpcHandler, UserBuilder } from "@a2a-js/sdk/server/express";

import { Coordinator } from "@brightbeamai/chap-coordinator";
import {
  makeChapAgentCard,
  makeChapAgentExecutor,
} from "@brightbeamai/chap-coordinator-a2a";

function parsePort(): number {
  const idx = process.argv.indexOf("--port");
  if (idx >= 0 && process.argv[idx + 1]) {
    const n = parseInt(process.argv[idx + 1], 10);
    if (!Number.isNaN(n)) return n;
  }
  return 9090;
}

const port = parsePort();
const baseUrl = `http://localhost:${port}/`;

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
  version: "0.3.4",
});

const executor = makeChapAgentExecutor(coord);
const taskStore = new InMemoryTaskStore();

const handler = new DefaultRequestHandler(card, taskStore, executor);
const app = express();
// legacyCompat serves a client on A2A 0.3 at the same endpoint: the card in
// its 0.3 shape when the request names no A2A-Version, and message/send.
app.use(`/${AGENT_CARD_PATH}`, agentCardHandler({ agentCardProvider: handler, legacyCompat: { enabled: true } }));
app.use("/", jsonRpcHandler({
  requestHandler: handler,
  userBuilder: UserBuilder.noAuthentication,
  legacyCompat: { enabled: true },
}));

process.stderr.write(`CHAP A2A reference server starting on ${baseUrl}\n`);
process.stderr.write(`Agent Card: ${baseUrl}${AGENT_CARD_PATH}\n`);
process.stderr.write(
  "Profiles enabled: core, review, whisper, deliberation, handoff, control, routing, audit-scitt.\n",
);

const host = process.env.CHAP_HOST ?? "127.0.0.1";
if (!["127.0.0.1", "::1", "localhost"].includes(host)) {
  process.stderr.write(
    `WARNING: binding to ${host} exposes an unauthenticated coordinator to ` +
    "the network. Use only on a trusted network.\n",
  );
}
app.listen(port, host);
