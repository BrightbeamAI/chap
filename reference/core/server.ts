/**
 * CHAP Core reference server - weekend-implementation.
 *
 * Implements all 7 Core methods, in-memory state, plain HTTP+JSON-RPC.
 * No crypto, no profiles. ~300 lines.
 *
 * Run:  npm install && npm run start:demo
 * Test: curl -X POST http://localhost:8080/chap -d '<envelope>'
 *
 * For production, swap the in-memory state for a database, add a real
 * auth layer (bearer or mTLS), and pick the profiles you need from
 * ../../profiles/.
 */

import { createServer, IncomingMessage, ServerResponse } from "node:http";

// ============================================================
//   Types
// ============================================================

type ParticipantUri = string;
type WorkspaceId    = string;
type TaskId         = string;

interface Envelope {
  jsonrpc: "2.0";
  id?: string | number;
  method?: string;
  params?: Record<string, unknown>;
  result?: unknown;
  error?:  { code: number; message: string; data?: unknown };
}

interface Member {
  uri:    ParticipantUri;
  type:   "human" | "agent" | "service" | "group" | "workspace";
  role:   string;
  joined: string;
  display_name?: string;
  capabilities?: Record<string, unknown>;
}

type TaskState = "created" | "in_progress" | "completed" | "declined";

interface Task {
  id:       TaskId;
  kind:     string;
  state:    TaskState;
  assignee: ParticipantUri;
  delegator: ParticipantUri;
  input:    Record<string, unknown>;
  output?:  unknown;
  confidence?: number;
  deadline?: string;
  created_at: string;
  updated_at: string;
  history:   { ts: string; from: ParticipantUri; state: TaskState; note?: string }[];
}

interface AuditEntry {
  seq:      number;
  arrived:  string;
  envelope: Envelope;
}

interface Workspace {
  id:           WorkspaceId;
  created:      string;
  state:        "active" | "paused" | "closed";
  members:      Map<ParticipantUri, Member>;
  tasks:        Map<TaskId, Task>;
  audit:        AuditEntry[];
  profiles:     string[];
}

// ============================================================
//   Error codes (JSON-RPC 2.0)
// ============================================================

const E = {
  PARSE:    -32700,
  REQUEST:  -32600,
  METHOD:   -32601,
  PARAMS:   -32602,
  INTERNAL: -32603,
} as const;

function err(code: number, message: string, data?: unknown) {
  return { code, message, ...(data !== undefined ? { data } : {}) };
}

// ============================================================
//   State (in-memory; replace for production)
// ============================================================

const workspaces = new Map<WorkspaceId, Workspace>();

function getWorkspace(id: string): Workspace | null {
  return workspaces.get(id) ?? null;
}

function ensureWorkspace(id: string): Workspace {
  let ws = workspaces.get(id);
  if (!ws) {
    ws = {
      id,
      created:  new Date().toISOString(),
      state:    "active",
      members:  new Map(),
      tasks:    new Map(),
      audit:    [],
      profiles: ["core/1.0"],
    };
    workspaces.set(id, ws);
  }
  return ws;
}

function ulid(): string {
  // Compact ULID-ish; good enough for the reference.
  const t = Date.now().toString(36).toUpperCase().padStart(10, "0");
  const r = Array.from({ length: 16 }, () =>
    "0123456789ABCDEFGHJKMNPQRSTVWXYZ"[Math.floor(Math.random() * 32)]
  ).join("");
  return (t + r).slice(0, 26);
}

function recordAudit(ws: Workspace, env: Envelope): void {
  ws.audit.push({
    seq: ws.audit.length,
    arrived: new Date().toISOString(),
    envelope: env,
  });
}

// ============================================================
//   Method handlers
// ============================================================

type Params = Record<string, unknown>;
type Handler = (p: Params) => { result?: unknown; error?: ReturnType<typeof err> };

function requireFields(p: Params, fields: string[]): string | null {
  for (const f of fields) {
    if (!(f in p)) return f;
  }
  return null;
}

const handlers: Record<string, Handler> = {

  "workspace.describe": (p) => {
    const missing = requireFields(p, ["workspace"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };

    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace: ${p.workspace}`) };

    return {
      result: {
        id:           ws.id,
        created:      ws.created,
        state:        ws.state,
        members:      Array.from(ws.members.values()),
        profiles:     ws.profiles,
        audit_count:  ws.audit.length,
      },
    };
  },

  "participant.join": (p) => {
    const missing = requireFields(p, ["workspace", "from", "type"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };

    const ws = ensureWorkspace(p.workspace as string);
    const uri  = p.from as string;
    const type = p.type as Member["type"];
    const role = (p.role as string) ?? "participant";

    ws.members.set(uri, {
      uri,
      type,
      role,
      joined: new Date().toISOString(),
      display_name: p.display_name as string,
      capabilities: p.capabilities as Record<string, unknown>,
    });

    return { result: { joined: true, as: uri, role } };
  },

  "participant.leave": (p) => {
    const missing = requireFields(p, ["workspace", "from"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };

    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace: ${p.workspace}`) };

    ws.members.delete(p.from as string);
    return { result: { left: true } };
  },

  "task.create": (p) => {
    const missing = requireFields(p, ["workspace", "from", "kind", "input"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };

    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace: ${p.workspace}`) };

    const assignee = (p.assignee as string) ?? (p.to as string);
    if (!assignee) return { error: err(E.PARAMS, "Missing assignee or to") };

    if (!ws.members.has(assignee)) {
      return { error: err(E.PARAMS, `Assignee not in workspace: ${assignee}`) };
    }

    const id = `tsk_${ulid()}`;
    const now = new Date().toISOString();
    const task: Task = {
      id,
      kind:       p.kind as string,
      state:      "created",
      assignee,
      delegator:  p.from as string,
      input:      p.input as Record<string, unknown>,
      deadline:   p.deadline as string,
      created_at: now,
      updated_at: now,
      history: [{ ts: now, from: p.from as string, state: "created" }],
    };
    ws.tasks.set(id, task);

    return { result: { task_id: id, state: "created" } };
  },

  "task.update": (p) => {
    const missing = requireFields(p, ["workspace", "from", "task_id", "state"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };

    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace: ${p.workspace}`) };

    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: err(E.PARAMS, `Unknown task: ${p.task_id}`) };

    const newState = p.state as TaskState;

    // State-machine validation: only allow legal transitions
    const legal: Record<TaskState, TaskState[]> = {
      created:     ["in_progress", "declined"],
      in_progress: ["in_progress", "completed", "declined"],
      completed:   [],
      declined:    [],
    };
    if (!legal[task.state].includes(newState)) {
      return { error: err(E.PARAMS, `Illegal transition ${task.state} → ${newState}`) };
    }

    task.state      = newState;
    task.updated_at = new Date().toISOString();
    task.history.push({
      ts:    task.updated_at,
      from:  p.from as string,
      state: newState,
      note:  p.progress_note as string,
    });

    return { result: { state: newState } };
  },

  "task.complete": (p) => {
    const missing = requireFields(p, ["workspace", "from", "task_id", "output"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };

    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace: ${p.workspace}`) };

    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: err(E.PARAMS, `Unknown task: ${p.task_id}`) };

    if (task.state === "completed" || task.state === "declined") {
      return { error: err(E.PARAMS, `Task is terminal: ${task.state}`) };
    }

    task.state      = "completed";
    task.output     = p.output;
    task.confidence = p.confidence as number;
    task.updated_at = new Date().toISOString();
    task.history.push({
      ts: task.updated_at,
      from: p.from as string,
      state: "completed",
    });

    return { result: { state: "completed" } };
  },

  "audit.read": (p) => {
    const missing = requireFields(p, ["workspace"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };

    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace: ${p.workspace}`) };

    const range  = (p.range  as { from_seq?: number; to_seq?: number }) ?? {};
    const filter = (p.filter as { method?: string; from?: string; task_id?: string }) ?? {};

    const fromSeq = range.from_seq ?? 0;
    const toSeq   = range.to_seq   ?? ws.audit.length;

    const entries = ws.audit
      .slice(fromSeq, toSeq)
      .filter((e) => {
        if (filter.method && e.envelope.method !== filter.method) return false;
        if (filter.from   && e.envelope.params?.from !== filter.from) return false;
        if (filter.task_id) {
          const tid = e.envelope.params?.task_id;
          if (tid !== filter.task_id) return false;
        }
        return true;
      });

    return {
      result: {
        entries,
        next_seq: toSeq,
      },
    };
  },
};

// ============================================================
//   Dispatch
// ============================================================

function dispatch(env: Envelope): Envelope {
  if (env.jsonrpc !== "2.0" || typeof env.method !== "string") {
    return { jsonrpc: "2.0", id: env.id ?? null, error: err(E.REQUEST, "Invalid JSON-RPC 2.0 request") };
  }

  const handler = handlers[env.method];
  if (!handler) {
    return { jsonrpc: "2.0", id: env.id ?? null, error: err(E.METHOD, `Unknown method: ${env.method}`) };
  }

  try {
    const params = env.params ?? {};
    const out = handler(params);

    // Audit every accepted envelope to its workspace.
    const wsId = params.workspace as string;
    if (wsId) {
      const ws = getWorkspace(wsId);
      if (ws && !out.error) recordAudit(ws, env);
    }

    if (out.error) {
      return { jsonrpc: "2.0", id: env.id ?? null, error: out.error };
    }
    return { jsonrpc: "2.0", id: env.id ?? null, result: out.result };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return { jsonrpc: "2.0", id: env.id ?? null, error: err(E.INTERNAL, msg) };
  }
}

// ============================================================
//   HTTP server
// ============================================================

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data",   (c) => chunks.push(c));
    req.on("end",    () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error",  reject);
  });
}

function reply(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify(body));
}

const server = createServer(async (req, res) => {
  if (req.method !== "POST" || req.url !== "/chap") {
    return reply(res, 404, { error: "POST /chap" });
  }

  let env: Envelope;
  try {
    const raw = await readBody(req);
    env = JSON.parse(raw);
  } catch {
    return reply(res, 400, { jsonrpc: "2.0", id: null, error: err(E.PARSE, "Malformed JSON") });
  }

  const response = dispatch(env);
  reply(res, response.error ? 400 : 200, response);
});

const port = parseInt(process.env.PORT ?? "8080", 10);
server.listen(port, () => {
  console.log(`CHAP Core reference listening on http://localhost:${port}/chap`);
  console.log(`Try: curl -X POST http://localhost:${port}/chap -H 'Content-Type: application/json' \\`);
  console.log(`     -d '{"jsonrpc":"2.0","id":"1","method":"workspace.describe","params":{"workspace":"wsp_demo"}}'`);
});
