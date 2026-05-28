/**
 * CHAP Core + Review reference server.
 *
 * Implements the 7 Core methods plus the 6 review-profile methods:
 *   review.request, decide.approve, decide.reject, decide.override,
 *   abstain.declare, escalate.raise.
 *
 * decide.override carries an RFC 6902 JSON Patch + rationale + tags +
 * policy_refs. This is where CHAP's structured-override-as-learning-
 * data dividend lives.
 *
 * In-memory state, plain HTTP + JSON-RPC 2.0. No external deps beyond
 * Node 18+ built-ins. ~500 lines.
 */

import { createServer, IncomingMessage, ServerResponse } from "node:http";

// ============================================================
//   Types
// ============================================================

type ParticipantUri = string;
type WorkspaceId    = string;
type TaskId         = string;
type ArtefactId     = string;

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

type TaskState =
  | "created"
  | "in_progress"
  | "review_requested"
  | "completed"
  | "declined"
  | "abstained"
  | "escalated"
  | "superseded";

interface JsonPatchOp {
  op: "add" | "replace" | "remove" | "copy" | "move" | "test";
  path: string;
  value?: unknown;
  from?: string;
}

interface OverrideArtefact {
  id:                ArtefactId;
  task_id:           TaskId;
  reviewer:          ParticipantUri;
  based_on_artefact: unknown;       // The draft being overridden
  diff:              JsonPatchOp[]; // RFC 6902
  result:            unknown;       // The diff applied to the draft
  rationale:         string;
  tags:              string[];
  policy_refs:       string[];
  ts:                string;
  // CHAP 0.2.1 — optional artefact-identity fields (SPEC §9.2.1, §9.4).
  // logical_id: durable handle for the thing the artefact is about,
  //             shared across revisions/overrides/supersessions.
  // instance_id: stable handle for this specific version; when present,
  //              must equal content_hash or be derived from it.
  // intent_preserved: when based_on carries a logical_id, true means
  //                   the override refines the same underlying decision;
  //                   false means a different decision substituted.
  logical_id?:       string;
  instance_id?:      string;
  intent_preserved?: boolean;
}

interface Task {
  id:         TaskId;
  kind:       string;
  state:      TaskState;
  assignee:   ParticipantUri;
  delegator:  ParticipantUri;
  input:      Record<string, unknown>;
  output?:    unknown;
  confidence?: number;
  deadline?:  string;
  created_at: string;
  updated_at: string;
  review?: {
    requested_at: string;
    requested_to: ParticipantUri[];
    rule:         string;
    deadline?:    string;
    decisions:    {
      reviewer: ParticipantUri;
      kind:     "approve" | "reject" | "override" | "abstain";
      ts:       string;
      comment?: string;
      tags?:    string[];
      override_artefact_id?: ArtefactId;
      abstain_category?: string;
    }[];
  };
  history:    { ts: string; from: ParticipantUri; state: TaskState; note?: string }[];
  supersedes?: TaskId;
}

interface AuditEntry {
  seq:      number;
  arrived:  string;
  envelope: Envelope;
}

interface Workspace {
  id:        WorkspaceId;
  created:   string;
  state:     "active" | "paused" | "closed";
  members:   Map<ParticipantUri, Member>;
  tasks:     Map<TaskId, Task>;
  overrides: Map<ArtefactId, OverrideArtefact>;
  audit:     AuditEntry[];
  profiles:  string[];
}

// ============================================================
//   Error codes
// ============================================================

const E = {
  PARSE:    -32700,
  REQUEST:  -32600,
  METHOD:   -32601,
  PARAMS:   -32602,
  INTERNAL: -32603,
  // review-profile codes
  NOT_REVIEWABLE:   -32010,
  NOT_AUTHORISED:   -32011,
  PATCH_FAILED:     -32012,
  REVIEW_LAPSED:    -32013,
} as const;

function err(code: number, message: string, data?: unknown) {
  return { code, message, ...(data !== undefined ? { data } : {}) };
}

// ============================================================
//   Minimal RFC 6902 JSON Patch — apply
// ============================================================

function applyJsonPatch(doc: unknown, ops: JsonPatchOp[]): unknown {
  // Deep-clone the document to avoid mutating the original.
  const target = JSON.parse(JSON.stringify(doc));

  for (const op of ops) {
    const parts = op.path.split("/").slice(1).map((p) =>
      p.replace(/~1/g, "/").replace(/~0/g, "~")
    );
    if (parts.length === 0) {
      throw new Error(`Cannot operate on root path`);
    }

    let parent: any = target;
    for (let i = 0; i < parts.length - 1; i++) {
      const key = Array.isArray(parent) ? parseInt(parts[i], 10) : parts[i];
      if (parent[key] === undefined) {
        throw new Error(`Path not found: ${op.path}`);
      }
      parent = parent[key];
    }

    const lastKey = Array.isArray(parent)
      ? parseInt(parts[parts.length - 1], 10)
      : parts[parts.length - 1];

    switch (op.op) {
      case "add":
      case "replace":
        parent[lastKey] = op.value;
        break;
      case "remove":
        if (Array.isArray(parent)) parent.splice(lastKey as number, 1);
        else delete parent[lastKey];
        break;
      default:
        throw new Error(`Unsupported op for this teaching reference: ${op.op}`);
    }
  }

  return target;
}

// ============================================================
//   State
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
      created:   new Date().toISOString(),
      state:     "active",
      members:   new Map(),
      tasks:     new Map(),
      overrides: new Map(),
      audit:     [],
      profiles:  ["core/1.0", "review/1.0"],
    };
    workspaces.set(id, ws);
  }
  return ws;
}

function ulid(): string {
  const t = Date.now().toString(36).toUpperCase().padStart(10, "0");
  const r = Array.from({ length: 16 }, () =>
    "0123456789ABCDEFGHJKMNPQRSTVWXYZ"[Math.floor(Math.random() * 32)]
  ).join("");
  return (t + r).slice(0, 26);
}

function recordAudit(ws: Workspace, env: Envelope): void {
  ws.audit.push({
    seq:      ws.audit.length,
    arrived:  new Date().toISOString(),
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

  // -------- Core --------

  "workspace.describe": (p) => {
    const missing = requireFields(p, ["workspace"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace: ${p.workspace}`) };
    return {
      result: {
        id:          ws.id,
        created:     ws.created,
        state:       ws.state,
        members:     Array.from(ws.members.values()),
        profiles:    ws.profiles,
        audit_count: ws.audit.length,
        task_count:  ws.tasks.size,
        override_count: ws.overrides.size,
      },
    };
  },

  "participant.join": (p) => {
    const missing = requireFields(p, ["workspace", "from", "type"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
    const ws = ensureWorkspace(p.workspace as string);
    const uri  = p.from as string;
    ws.members.set(uri, {
      uri,
      type:         p.type as Member["type"],
      role:         (p.role as string) ?? "participant",
      joined:       new Date().toISOString(),
      display_name: p.display_name as string,
      capabilities: p.capabilities as Record<string, unknown>,
    });
    return { result: { joined: true, as: uri } };
  },

  "participant.leave": (p) => {
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };
    ws.members.delete(p.from as string);
    return { result: { left: true } };
  },

  "task.create": (p) => {
    const missing = requireFields(p, ["workspace", "from", "kind", "input"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };

    const assignee = (p.assignee as string) ?? (p.to as string);
    if (!assignee || !ws.members.has(assignee)) {
      return { error: err(E.PARAMS, `Assignee not in workspace`) };
    }

    const id = `tsk_${ulid()}`;
    const now = new Date().toISOString();
    ws.tasks.set(id, {
      id, kind: p.kind as string, state: "created",
      assignee, delegator: p.from as string,
      input: p.input as Record<string, unknown>,
      deadline: p.deadline as string,
      created_at: now, updated_at: now,
      history: [{ ts: now, from: p.from as string, state: "created" }],
    });
    return { result: { task_id: id, state: "created" } };
  },

  "task.update": (p) => {
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: err(E.PARAMS, `Unknown task`) };

    const newState = p.state as TaskState;
    const legal: Partial<Record<TaskState, TaskState[]>> = {
      created:          ["in_progress", "declined"],
      in_progress:      ["in_progress", "completed", "declined", "review_requested"],
      review_requested: ["in_progress"],
    };
    if (!legal[task.state]?.includes(newState)) {
      return { error: err(E.PARAMS, `Illegal transition ${task.state} → ${newState}`) };
    }

    task.state      = newState;
    task.updated_at = new Date().toISOString();
    task.history.push({
      ts: task.updated_at, from: p.from as string, state: newState,
      note: p.progress_note as string,
    });
    return { result: { state: newState } };
  },

  "task.complete": (p) => {
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: err(E.PARAMS, `Unknown task`) };

    if (task.state === "completed" || task.state === "declined") {
      return { error: err(E.PARAMS, `Task is terminal: ${task.state}`) };
    }

    task.output     = p.output;
    task.confidence = p.confidence as number;
    task.state      = "completed";
    task.updated_at = new Date().toISOString();
    task.history.push({ ts: task.updated_at, from: p.from as string, state: "completed" });
    return { result: { state: "completed" } };
  },

  "audit.read": (p) => {
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };

    const range  = (p.range  as { from_seq?: number; to_seq?: number }) ?? {};
    const filter = (p.filter as { method?: string; from?: string; task_id?: string }) ?? {};
    const fromSeq = range.from_seq ?? 0;
    const toSeq   = range.to_seq   ?? ws.audit.length;

    const entries = ws.audit
      .slice(fromSeq, toSeq)
      .filter((e) => {
        const params = e.envelope.params as Params | undefined;
        if (filter.method && e.envelope.method !== filter.method) return false;
        if (filter.from && params?.from !== filter.from) return false;
        if (filter.task_id && params?.task_id !== filter.task_id) return false;
        return true;
      });

    return { result: { entries, next_seq: toSeq } };
  },

  // -------- Review profile --------

  "review.request": (p) => {
    const missing = requireFields(p, ["workspace", "from", "task_id", "artefact"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };

    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: err(E.PARAMS, `Unknown task`) };

    const reviewers = Array.isArray(p.to) ? (p.to as string[]) : [p.to as string];
    task.state = "review_requested";
    task.updated_at = new Date().toISOString();
    task.review = {
      requested_at: task.updated_at,
      requested_to: reviewers,
      rule:         (p.rule as string) ?? "any_one_approves",
      deadline:     p.deadline as string,
      decisions:    [],
    };
    task.history.push({ ts: task.updated_at, from: p.from as string, state: "review_requested" });
    // Stash artefact on the task for diff base lookup
    (task as any).pending_artefact = p.artefact;
    return { result: { state: "review_requested", review_id: task.id } };
  },

  "decide.approve": (p) => {
    return decide(p, "approve");
  },

  "decide.reject": (p) => {
    return decide(p, "reject");
  },

  "decide.override": (p) => {
    const missing = requireFields(p, ["workspace", "from", "task_id", "diff", "rationale"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: err(E.PARAMS, `Unknown task`) };
    if (task.state !== "review_requested") {
      return { error: err(E.NOT_REVIEWABLE, `Task not awaiting review: ${task.state}`) };
    }

    const baseArtefact = p.based_on_artefact ?? (task as any).pending_artefact;
    if (baseArtefact === undefined) {
      return { error: err(E.PARAMS, `No base artefact for override`) };
    }

    const diff = p.diff as JsonPatchOp[];
    let applied: unknown;
    try {
      applied = applyJsonPatch(baseArtefact, diff);
    } catch (e) {
      return { error: err(E.PATCH_FAILED, (e as Error).message) };
    }

    const artefactId = `art_${ulid()}`;
    const override: OverrideArtefact = {
      id:                artefactId,
      task_id:           task.id,
      reviewer:          p.from as string,
      based_on_artefact: baseArtefact,
      diff,
      result:            applied,
      rationale:         p.rationale as string,
      tags:              (p.tags as string[]) ?? [],
      policy_refs:       (p.policy_refs as string[]) ?? [],
      ts:                new Date().toISOString(),
      // CHAP 0.2.1 — pass through optional identity / intent fields if present.
      // The reference does not synthesise these; clients that want
      // version-graph projection should send them, in which case the
      // override should carry the same logical_id as the based_on
      // artefact and set intent_preserved explicitly.
      ...(p.logical_id       !== undefined ? { logical_id:       p.logical_id       as string  } : {}),
      ...(p.instance_id      !== undefined ? { instance_id:      p.instance_id      as string  } : {}),
      ...(p.intent_preserved !== undefined ? { intent_preserved: p.intent_preserved as boolean } : {}),
    };
    ws.overrides.set(artefactId, override);

    task.review!.decisions.push({
      reviewer: p.from as string,
      kind:     "override",
      ts:       override.ts,
      tags:     override.tags,
      override_artefact_id: artefactId,
    });
    task.output = applied;
    task.state = "completed";
    task.updated_at = override.ts;
    task.history.push({ ts: override.ts, from: p.from as string, state: "completed", note: "override applied" });

    return { result: { state: "completed", override_artefact_id: artefactId, applied } };
  },

  "abstain.declare": (p) => {
    const missing = requireFields(p, ["workspace", "from", "task_id", "reason"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: err(E.PARAMS, `Unknown task`) };
    if (task.state !== "review_requested") {
      return { error: err(E.NOT_REVIEWABLE, `Task not awaiting review: ${task.state}`) };
    }

    const now = new Date().toISOString();
    task.review!.decisions.push({
      reviewer: p.from as string,
      kind:     "abstain",
      ts:       now,
      comment:  p.reason as string,
      abstain_category: (p.category as string) ?? "other",
    });
    task.state = "abstained";
    task.updated_at = now;
    task.history.push({ ts: now, from: p.from as string, state: "abstained", note: p.reason as string });
    return { result: { state: "abstained" } };
  },

  "escalate.raise": (p) => {
    const missing = requireFields(p, ["workspace", "from", "original_task_id", "new_task"]);
    if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
    const ws = getWorkspace(p.workspace as string);
    if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };
    const orig = ws.tasks.get(p.original_task_id as string);
    if (!orig) return { error: err(E.PARAMS, `Unknown original task`) };

    const nt = p.new_task as Record<string, unknown>;
    const assignee = nt.assignee as string;
    if (!assignee || !ws.members.has(assignee)) {
      return { error: err(E.PARAMS, `Escalation assignee not in workspace`) };
    }

    const newId = `tsk_${ulid()}`;
    const now = new Date().toISOString();
    ws.tasks.set(newId, {
      id: newId, kind: nt.kind as string, state: "created",
      assignee, delegator: p.from as string,
      input: (nt.input as Record<string, unknown>) ?? {},
      created_at: now, updated_at: now,
      supersedes: orig.id,
      history: [{ ts: now, from: p.from as string, state: "created", note: `escalated from ${orig.id}` }],
    });
    orig.state = "escalated";
    orig.updated_at = now;
    orig.history.push({ ts: now, from: p.from as string, state: "escalated", note: `→ ${newId}` });

    return { result: { new_task_id: newId, escalated_from: orig.id } };
  },
};

function decide(p: Params, kind: "approve" | "reject"): { result?: unknown; error?: ReturnType<typeof err> } {
  const missing = requireFields(p, ["workspace", "from", "task_id"]);
  if (missing) return { error: err(E.PARAMS, `Missing field: ${missing}`) };
  const ws = getWorkspace(p.workspace as string);
  if (!ws) return { error: err(E.PARAMS, `Unknown workspace`) };
  const task = ws.tasks.get(p.task_id as string);
  if (!task) return { error: err(E.PARAMS, `Unknown task`) };
  if (task.state !== "review_requested") {
    return { error: err(E.NOT_REVIEWABLE, `Task not awaiting review: ${task.state}`) };
  }

  const now = new Date().toISOString();
  task.review!.decisions.push({
    reviewer: p.from as string,
    kind,
    ts:       now,
    comment:  p.comment as string,
    tags:     (p.tags as string[]) ?? [],
  });
  if (kind === "approve") {
    task.output = (task as any).pending_artefact;
    task.state  = "completed";
  } else {
    if (p.request_revision) task.state = "in_progress";
    else task.state = "declined";
  }
  task.updated_at = now;
  task.history.push({ ts: now, from: p.from as string, state: task.state });
  return { result: { state: task.state } };
}

// ============================================================
//   Dispatch
// ============================================================

function dispatch(env: Envelope): Envelope {
  if (env.jsonrpc !== "2.0" || typeof env.method !== "string") {
    return { jsonrpc: "2.0", id: env.id ?? null as any, error: err(E.REQUEST, "Invalid JSON-RPC 2.0 request") };
  }
  const handler = handlers[env.method];
  if (!handler) {
    return { jsonrpc: "2.0", id: env.id ?? null as any, error: err(E.METHOD, `Unknown method: ${env.method}`) };
  }
  try {
    const params = env.params ?? {};
    const out = handler(params);
    const wsId = params.workspace as string;
    if (wsId) {
      const ws = getWorkspace(wsId);
      if (ws && !out.error) recordAudit(ws, env);
    }
    if (out.error) return { jsonrpc: "2.0", id: env.id ?? null as any, error: out.error };
    return { jsonrpc: "2.0", id: env.id ?? null as any, result: out.result };
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return { jsonrpc: "2.0", id: env.id ?? null as any, error: err(E.INTERNAL, msg) };
  }
}

// ============================================================
//   HTTP server
// ============================================================

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data",  (c) => chunks.push(c));
    req.on("end",   () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

function reply(res: ServerResponse, status: number, body: unknown): void {
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.end(JSON.stringify(body));
}

const server = createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
    res.end();
    return;
  }
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
  console.log(`CHAP Core+Review reference on http://localhost:${port}/chap`);
  console.log(`Profiles: core/1.0, review/1.0`);
});
