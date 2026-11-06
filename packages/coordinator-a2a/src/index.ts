/**
 * @brightbeamai/chap-coordinator-a2a
 *
 * A2A server adapter for a CHAP Coordinator. Wraps a Coordinator
 * instance and exposes every CHAP method as an A2A skill.
 *
 * Spec target: A2A 1.0, through @a2a-js/sdk 1.x, with v0.3 clients
 * served by the SDK's compatibility layer. CHAP 0.3.
 *
 * Usage::
 *
 *   import express from "express";
 *   import { Coordinator } from "@brightbeamai/chap-coordinator";
 *   import { makeChapAgentCard, makeChapAgentExecutor } from "@brightbeamai/chap-coordinator-a2a";
 *   import { DefaultRequestHandler, InMemoryTaskStore } from "@a2a-js/sdk/server";
 *   import { AGENT_CARD_PATH } from "@a2a-js/sdk";
 *   import { agentCardHandler, jsonRpcHandler, UserBuilder } from "@a2a-js/sdk/server/express";
 *
 *   const coord = new Coordinator({ ... });
 *   const card = makeChapAgentCard({ baseUrl: "http://127.0.0.1:9090" });
 *   const handler = new DefaultRequestHandler(card, new InMemoryTaskStore(), makeChapAgentExecutor(coord));
 *
 *   const app = express();
 *   app.use(`/${AGENT_CARD_PATH}`, agentCardHandler({ agentCardProvider: handler, legacyCompat: { enabled: true } }));
 *   app.use("/", jsonRpcHandler({ requestHandler: handler, userBuilder: UserBuilder.noAuthentication,
 *                                 legacyCompat: { enabled: true } }));
 *   app.listen(9090, "127.0.0.1");
 *
 * See reference/a2a-server-ts/server.ts for a runnable reference.
 *
 * Architecture notes
 * ------------------
 *
 * - One Coordinator, one A2A agent. Multi-workspace is handled inside
 *   the Coordinator.
 * - The adapter holds no state. Each A2A ``SendMessage`` translates
 *   to a JSON-RPC envelope and dispatches through coord.dispatch().
 * - Skill ids match the MCP transport's tool names (chap.<method>) so
 *   a caller fluent in one is fluent in the other.
 * - Authentication is out of scope. A2A's security schemes attach to
 *   the Agent Card and are enforced at the HTTP transport.
 */

export { makeChapAgentCard, CHAP_MEDIA_TYPE } from "./card.js";
export type { ChapAgentCardOptions } from "./card.js";

export {
  ChapAgentExecutor,
  makeChapAgentExecutor,
  dispatchA2aMessage,
} from "./executor.js";
export type { ChapAgentExecutorOptions, DispatchA2aMessageOptions } from "./executor.js";
