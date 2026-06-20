/**
 * @chap/coordinator-a2a
 *
 * A2A server adapter for a CHAP Coordinator. Wraps a Coordinator
 * instance and exposes every CHAP method as an A2A skill.
 *
 * Spec target: A2A 0.3.0 (the version implemented by the @a2a-js/sdk
 * SDK we depend on). CHAP 0.2.
 *
 * Usage::
 *
 *   import { Coordinator } from "@chap/coordinator";
 *   import { makeChapAgentCard, makeChapAgentExecutor } from "@chap/coordinator-a2a";
 *   import { DefaultRequestHandler, InMemoryTaskStore } from "@a2a-js/sdk/server";
 *   import { A2AExpressApp } from "@a2a-js/sdk/server/express";
 *   import express from "express";
 *
 *   const coord = new Coordinator({ ... });
 *   const card = makeChapAgentCard({ baseUrl: "http://localhost:9090" });
 *   const executor = makeChapAgentExecutor(coord);
 *
 *   const handler = new DefaultRequestHandler(card, new InMemoryTaskStore(), executor);
 *   const app = new A2AExpressApp(handler).setupRoutes(express(), "");
 *   app.listen(9090);
 *
 * See reference/a2a-server-ts/server.ts for a runnable reference.
 *
 * Architecture notes
 * ------------------
 *
 * - One Coordinator, one A2A agent. Multi-workspace is handled inside
 *   the Coordinator.
 * - The adapter holds no state. Each A2A ``message/send`` translates
 *   to a JSON-RPC envelope and dispatches through coord.dispatch().
 * - Skill ids match the MCP transport's tool names (chap.<method>) so
 *   a caller fluent in one is fluent in the other.
 * - Authentication is out of scope. A2A's security schemes attach to
 *   the Agent Card and are enforced at the HTTP transport.
 */

export { makeChapAgentCard } from "./card.js";
export type { ChapAgentCardOptions } from "./card.js";

export {
  ChapAgentExecutor,
  makeChapAgentExecutor,
  dispatchA2aMessage,
} from "./executor.js";
export type { ChapAgentExecutorOptions } from "./executor.js";
