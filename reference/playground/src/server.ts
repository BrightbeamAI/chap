/**
 * Playground server.
 *
 * Routes:
 *   GET  /                   → static UI (role picker → role UIs)
 *   GET  /playground.js
 *   GET  /playground.css
 *   POST /rpc                → JSON-RPC endpoint (the real CHAP wire)
 *   GET  /events?participant=<uri>
 *                            → Server-Sent Events stream of envelopes
 *                              relevant to this participant
 *   GET  /api/workspace      → convenience: current workspace state
 *   GET  /api/audit?from_seq → convenience: audit entries
 *   GET  /api/tickets        → the ticket catalogue (for demo display)
 *   POST /api/reset          → wipe state, restart bot processing
 *   GET  /api/health         → Ollama probe + workspace counts
 */

import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

import {
  Coordinator,
  E,
  type Envelope,
  rpcError,
} from "@chap/coordinator";
import { makePlaygroundPolicies } from "./policies.js";

import { makeFileStateStore } from "./state-store.js";
import {
  BOT_URI,
  probeOllama,
  processAllTickets,
  draftResponse,
} from "./ollama-agent.js";
import { TICKETS } from "./tickets.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const PORT          = parseInt(process.env.PORT ?? "7777", 10);
const WORKSPACE_ID  = "wsp_techcorp_support";
const MAYA_URI      = "human:maya@local";
const SAM_URI       = "human:sam@local";
const PUBLIC_DIR    = path.join(__dirname, "public");
const DATA_DIR      = path.join(__dirname, "..", "data");

// ============================================================
//   Coordinator + state
// ============================================================

const coord = new Coordinator({
  ...makePlaygroundPolicies(SAM_URI),
  onAutoEscalate: (task, to) => {
    console.log(`[routing] auto-escalated task ${task.id} to ${to}`);
  },
});

const store = makeFileStateStore(coord, DATA_DIR);

// ============================================================
//   SSE fan-out: per-participant subscriber lists
// ============================================================

const sseClients = new Map<string, Set<ServerResponse>>();

function sseSubscribe(uri: string, res: ServerResponse): () => void {
  let set = sseClients.get(uri);
  if (!set) { set = new Set(); sseClients.set(uri, set); }
  set.add(res);
  return () => {
    set!.delete(res);
    if (set!.size === 0) sseClients.delete(uri);
  };
}

function sseBroadcast(uri: string, data: unknown) {
  const set = sseClients.get(uri);
  if (!set) return;
  const payload = `data: ${JSON.stringify(data)}\n\n`;
  for (const res of set) {
    try { res.write(payload); } catch { /* client gone */ }
  }
}

// Wire the coordinator's audit listener to SSE fan-out + persistence.
coord.onAudit((ws, entry) => {
  // Decide which participants care about this entry.
  // Simple rule: anything in this workspace is broadcast to all
  // currently-connected SSE clients of any participant in this
  // workspace. Real systems would filter by addressing; this is a
  // demo with 2 humans + 1 bot.
  for (const uri of sseClients.keys()) {
    sseBroadcast(uri, { kind: "audit", seq: entry.seq, envelope: entry.envelope, ts: entry.arrived });
  }
  // Persist asynchronously; if it fails we log but don't break dispatch.
  store.save().catch((e) => console.warn("state-store: save failed", e));
});

// ============================================================
//   Workspace bootstrap
// ============================================================

async function bootstrap(): Promise<void> {
  await store.load();
  const existing = coord.getWorkspace(WORKSPACE_ID);
  if (existing) {
    console.log(`[boot] loaded workspace ${WORKSPACE_ID} (audit length ${existing.audit.length})`);
    return;
  }

  // Fresh workspace.
  coord.dispatch({
    jsonrpc: "2.0", id: "boot-1", method: "workspace.create",
    params: {
      workspace: WORKSPACE_ID,
      profiles: ["core/1.0", "review/1.0", "routing/1.0"],
    },
  });

  // Join the three participants.
  coord.dispatch({
    jsonrpc: "2.0", id: "boot-2", method: "participant.join",
    params: { workspace: WORKSPACE_ID, from: MAYA_URI, type: "human", role: "front-line", display_name: "Maya" },
  });
  coord.dispatch({
    jsonrpc: "2.0", id: "boot-3", method: "participant.join",
    params: { workspace: WORKSPACE_ID, from: SAM_URI, type: "human", role: "senior", display_name: "Sam" },
  });
  coord.dispatch({
    jsonrpc: "2.0", id: "boot-4", method: "participant.join",
    params: { workspace: WORKSPACE_ID, from: BOT_URI, type: "agent", role: "drafter", display_name: "Triage Bot" },
  });

  // Kick off the bot processing every ticket. This runs in the
  // background so the server can start serving immediately.
  void runBotProcessing();
}

async function runBotProcessing(): Promise<void> {
  const probe = await probeOllama();
  if (!probe.ok) {
    console.warn(`[bot] Ollama not available: ${probe.detail}`);
    console.warn(`[bot] Tickets will not be drafted. Install Ollama and pull the model, then POST /api/reset.`);
    return;
  }
  console.log(`[bot] ${probe.detail}; drafting ${TICKETS.length} tickets`);
  try {
    await processAllTickets(coord, WORKSPACE_ID, TICKETS, MAYA_URI, {
      drafter: draftResponse,
      onProgress: (i, total) => console.log(`[bot] ${i}/${total} tickets drafted`),
    });
  } catch (e) {
    console.error("[bot] processing failed:", e);
  }
}

// ============================================================
//   HTTP request handling
// ============================================================

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js":   "application/javascript; charset=utf-8",
  ".css":  "text/css; charset=utf-8",
  ".svg":  "image/svg+xml",
  ".json": "application/json; charset=utf-8",
};

async function serveStatic(res: ServerResponse, fileName: string): Promise<void> {
  try {
    // Prevent directory traversal.
    const safe = fileName.replace(/\.\./g, "").replace(/^\/+/, "");
    const full = path.join(PUBLIC_DIR, safe || "index.html");
    const buf  = await readFile(full);
    const ext  = path.extname(full).toLowerCase();
    res.statusCode = 200;
    res.setHeader("Content-Type", MIME[ext] ?? "application/octet-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.end(buf);
  } catch {
    res.statusCode = 404;
    res.end("Not found");
  }
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data",  (c: Buffer) => chunks.push(c));
    req.on("end",   () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

function jsonReply(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Access-Control-Allow-Origin",  "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.end(JSON.stringify(body));
}

const server = createServer(async (req, res) => {
  let url: URL;
  try {
    url = new URL(req.url ?? "/", `http://${req.headers.host}`);
  } catch {
    res.statusCode = 400;
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify({ error: "malformed URL" }));
    return;
  }
  const pathname = url.pathname;

  // CORS preflight
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.setHeader("Access-Control-Allow-Origin",  "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    res.end();
    return;
  }

  // SSE event stream
  if (req.method === "GET" && pathname === "/events") {
    const participant = url.searchParams.get("participant") ?? "anonymous";
    res.statusCode = 200;
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.write(`: connected as ${participant}\n\n`);
    // Heartbeat to keep proxies happy.
    const hb = setInterval(() => { try { res.write(": hb\n\n"); } catch {} }, 20000);
    const unsub = sseSubscribe(participant, res);
    req.on("close", () => { unsub(); clearInterval(hb); });
    return;
  }

  // JSON-RPC endpoint. the real CHAP wire
  if (req.method === "POST" && pathname === "/rpc") {
    let env: Envelope;
    try {
      env = JSON.parse(await readBody(req));
    } catch {
      return jsonReply(res, 400, { jsonrpc: "2.0", id: null, error: rpcError(E.PARSE, "Malformed JSON") });
    }
    const response = coord.dispatch(env);
    return jsonReply(res, response.error ? 400 : 200, response);
  }

  // Convenience: workspace state
  if (req.method === "GET" && pathname === "/api/workspace") {
    const ws = coord.getWorkspace(WORKSPACE_ID);
    if (!ws) return jsonReply(res, 404, { error: "workspace not found" });
    return jsonReply(res, 200, {
      id: ws.id,
      created: ws.created,
      state: ws.state,
      profiles: ws.profiles,
      members: Array.from(ws.members.values()),
      evidence_head: ws.audit.length,
      tasks: Array.from(ws.tasks.values()),
      overrides: Array.from(ws.overrides.values()),
      route_decisions: Array.from(ws.route_decisions.values()),
    });
  }

  // Convenience: audit log (paginated)
  if (req.method === "GET" && pathname === "/api/audit") {
    const fromSeq = parseInt(url.searchParams.get("from_seq") ?? "0", 10);
    const limit   = parseInt(url.searchParams.get("limit")    ?? "200", 10);
    const ws = coord.getWorkspace(WORKSPACE_ID);
    if (!ws) return jsonReply(res, 404, { error: "workspace not found" });
    return jsonReply(res, 200, {
      from_seq: fromSeq,
      evidence_head: ws.audit.length,
      entries: ws.audit.slice(fromSeq, fromSeq + limit),
    });
  }

  // Ticket catalogue (for demo display)
  if (req.method === "GET" && pathname === "/api/tickets") {
    return jsonReply(res, 200, { tickets: TICKETS });
  }

  // Reset
  if (req.method === "POST" && pathname === "/api/reset") {
    await store.reset();
    // Tear down and rebuild the workspace in-memory.
    coord.restore([]);
    await bootstrap();
    return jsonReply(res, 200, { ok: true });
  }

  // Health
  if (req.method === "GET" && pathname === "/api/health") {
    const probe = await probeOllama();
    const ws = coord.getWorkspace(WORKSPACE_ID);
    return jsonReply(res, 200, {
      ollama: probe,
      workspace: ws ? { id: ws.id, members: ws.members.size, tasks: ws.tasks.size, audit_head: ws.audit.length } : null,
    });
  }

  // Static files
  if (req.method === "GET") {
    return serveStatic(res, pathname);
  }

  jsonReply(res, 405, { error: "method not allowed" });
});

bootstrap().then(() => {
  server.listen(PORT, () => {
    console.log(`CHAP playground at http://localhost:${PORT}/`);
    console.log(`  JSON-RPC wire: POST http://localhost:${PORT}/rpc`);
    console.log(`  SSE stream:    GET  http://localhost:${PORT}/events?participant=<uri>`);
    console.log(`  Open one tab as Maya, another as Sam.`);
  });
});
