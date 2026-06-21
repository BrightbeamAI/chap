/**
 * @chap/coordinator/coordinator
 *
 * The Coordinator class. CHAP protocol logic, packaged as a library
 * rather than a CLI server. Applications instantiate one, call
 * `dispatch(envelope)`, subscribe to audit events, and persist via
 * the provided hooks.
 *
 * Coverage:
 *   - Core (9 methods)
 *   - review/1.0 (6 methods)
 *   - whisper/1.0 (2 methods + lapse handling)
 *   - deliberation/1.0 (4 methods)
 *   - modes/1.0 (mode handling at task.create; trial forces review)
 *   - handoff/1.0 (3 methods with multi-task and group support)
 *   - control/1.0 (7 methods with task/participant/workspace scopes)
 *   - routing/1.0 (3 methods producing route_decision artefacts)
 *   - security-signed/1.0 (top-level `sig` field; key history)
 *   - audit-scitt/1.0 (statement assembly + local chain linkage)
 *   - identity-oidc/1.0 + identity-vc/1.0 (binding hooks at join)
 */

import { canonicalize, sha256Hex, ZERO_HASH } from "./canonical.js";
import { publicKeyFromJwk, verifyEnvelope } from "./crypto.js";
import { IdFactory } from "./ids.js";
import { E, isValidEnvelope, rpcError } from "./jsonrpc.js";
import { applyJsonPatch } from "./patch.js";
import type { Store, WorkspaceRecord } from "./storage/store.js";
import { MemoryStore } from "./storage/store.js";
import { makeApi, type CoordinatorApi } from "./api.js";
import type {
  ArtefactId,
  AuditEntry,
  AuditListener,
  Envelope,
  KeyRecord,
  Member,
  Mode,
  OverrideArtefact,
  ParticipantUri,
  Task,
  TaskId,
  Workspace,
  WorkspaceId,
} from "./types.js";
import { modeLE } from "./types.js";
import { registerAuditScitt } from "./profiles/audit_scitt.js";
import { registerControl } from "./profiles/control.js";
import { registerDeliberation } from "./profiles/deliberation.js";
import { registerHandoff } from "./profiles/handoff.js";
import { registerRouting } from "./profiles/routing.js";
import { registerSecuritySigned } from "./profiles/security_signed.js";
import { registerWhisper } from "./profiles/whisper.js";

// ============================================================
//   Options
// ============================================================

export type TokenVerifier = (token: string) => Record<string, unknown> | null;
export type CredentialVerifier = (presentation: Record<string, unknown>) => Record<string, unknown> | null;
export type ScittSubmitter = (statement: Record<string, unknown>) => Record<string, unknown> | null;
export type ScittReceiptVerifier = (receipt: Record<string, unknown>) => boolean;
export type RoutingPolicyFn = (task: Task, candidates: ParticipantUri[]) => {
  selected: ParticipantUri;
  rationale: Record<string, unknown>;
};
export type ReviewDepthPolicyFn = (task: Task, hints: Record<string, unknown>) => {
  depth: "skip" | "spot_check" | "full" | "escalated";
  sampling_probability?: number;
  rationale: Record<string, unknown>;
};
export type EscalationPolicyFn = (task: Task, hints: Record<string, unknown>) => {
  escalate: boolean;
  to?: ParticipantUri;
  triggered_rule?: Record<string, unknown>;
};

/** Methods classified as privileged for step-up auth. */
export const PRIVILEGED_METHODS = new Set<string>([
  "control.pause", "control.resume", "control.cancel", "control.supersede",
  "control.snapshot", "control.rollback", "control.set_mode_ceiling",
  "workspace.set_profiles",
  "participant.rotate_key", "participant.revoke_key",
]);

export interface CoordinatorOptions {
  deterministicIds?: boolean;
  deterministicClock?: boolean;
  enableChain?: boolean;
  requireSignatures?: boolean;
  enforceStepUp?: boolean;
  onAudit?: AuditListener;
  onAutoEscalate?: (task: Task, to: ParticipantUri) => void;
  verifyOidcToken?: TokenVerifier;
  verifyVc?: CredentialVerifier;
  scittSubmitter?: ScittSubmitter;
  verifyScittReceipt?: ScittReceiptVerifier;
  routingPolicy?: RoutingPolicyFn;
  reviewDepthPolicy?: ReviewDepthPolicyFn;
  escalationPolicy?: EscalationPolicyFn;
  defaultProfiles?: string[];
  /**
   * Persistence backend. Defaults to MemoryStore (no persistence).
   * Pass SqliteStore or any other Store implementation to persist
   * workspaces between process restarts. The Coordinator calls
   * `store.load()` once during `start()` and `store.save()` after
   * every successful mutation.
   */
  store?: Store;
}

// ============================================================
//   Helpers
// ============================================================

function nowIso(clockMs?: number): string {
  const d = clockMs === undefined ? new Date() : new Date(clockMs);
  return d.toISOString().replace(/\.(\d{3})Z$/, ".$1Z");
}

function missing(params: Record<string, unknown>, fields: string[]): string | null {
  for (const f of fields) if (!(f in params)) return f;
  return null;
}

function linkHash(envelope: Envelope, prev: string): string {
  return sha256Hex(Buffer.concat([canonicalize(envelope), Buffer.from(prev, "utf-8")]));
}

function reply(env: Envelope, body: { result?: unknown; error?: { code: number; message: string; data?: unknown } }): Envelope {
  const out: Envelope = { jsonrpc: "2.0", id: env.id };
  if (body.error !== undefined) out.error = body.error;
  else out.result = body.result;
  return out;
}

// ============================================================
//   Handler type for profile modules
// ============================================================

export type Handler = (params: Record<string, unknown>) => { result?: unknown; error?: { code: number; message: string; data?: unknown } };

// ============================================================
//   Coordinator
// ============================================================

export class Coordinator {
  readonly workspaces = new Map<WorkspaceId, Workspace>();
  readonly options: CoordinatorOptions;
  readonly ids: IdFactory;
  readonly store: Store;
  private wsVersions = new Map<WorkspaceId, number>();
  private clockMs?: number;
  private auditListeners: AuditListener[] = [];
  /** Method handler registry; profiles plug into this. */
  readonly handlers = new Map<string, Handler>();
  /** Custom lapse-check function (whisper/1.0). Set by the whisper profile. */
  checkWhisperLapses?: (workspaceId: string, now?: string) => Envelope[];
  /** Typed method facade. Defined via getter in the constructor. */
  readonly api!: CoordinatorApi;
  /** @internal cached facade instance */
  private _api?: CoordinatorApi;

  constructor(options: CoordinatorOptions = {}) {
    this.options = options;
    this.store = options.store ?? new MemoryStore();
    this.ids = new IdFactory(!!options.deterministicIds, 0n, 1_700_000_000_000);
    if (options.deterministicClock) this.clockMs = 1_700_000_000_000;
    if (options.onAudit) this.auditListeners.push(options.onAudit);

    this.registerCoreHandlers();
    this.registerProfileHandlers();

    // Lazily-initialised typed facade. Constructed on first access so
    // tests that don't use it pay nothing.
    Object.defineProperty(this, "api", {
      get: () => {
        if (!this._api) this._api = makeApi(this);
        return this._api;
      },
      enumerable: false,
      configurable: true,
    });

    // Rehydrate any persisted state synchronously. Async stores can
    // call `start()` instead; this fallback covers the common
    // synchronous Memory/Sqlite path with no async setup needed.
    try {
      const loaded = this.store.load();
      if (!(loaded instanceof Promise)) {
        this.applyRecords(loaded);
      }
    } catch {
      /* fall through; caller must invoke start() */
    }
  }

  /**
   * Asynchronously hydrate workspaces from the store. Required only for
   * stores whose `load()` returns a Promise. Idempotent.
   */
  async start(): Promise<void> {
    const records = await this.store.load();
    this.applyRecords(records);
  }

  private applyRecords(records: WorkspaceRecord[]): void {
    this.workspaces.clear();
    this.wsVersions.clear();
    for (const r of records) {
      this.restore([r.data]);
      this.wsVersions.set(r.id, r.version);
    }
  }

  /** Persist the current snapshot of one workspace to the store. */
  private persist(ws: Workspace): void {
    const next = (this.wsVersions.get(ws.id) ?? 0) + 1;
    this.wsVersions.set(ws.id, next);
    try {
      // snapshot() returns an array of all workspaces; we only need
      // this one, so slice it. Stores are per-workspace.
      const all = this.snapshot() as Array<Record<string, unknown>>;
      const data = all.find(w => w.id === ws.id);
      if (!data) return;
      const result = this.store.save({
        id: ws.id,
        data,
        version: next,
        updated_at: this.now(),
      });
      // Async stores: fire-and-forget; failures surface via process unhandled-rejection.
      // Sync stores: nothing to do.
      if (result instanceof Promise) {
        result.catch(() => { /* deliberately swallowed; stores should log */ });
      }
    } catch {
      // Persistence failures must not break dispatch. Audit listeners
      // can mirror the audit stream to a second sink for durability.
    }
  }

  // -- lifecycle -----------------------------------------------------

  now(): string {
    if (this.clockMs !== undefined) {
      this.clockMs += 1000;
      return nowIso(this.clockMs);
    }
    return nowIso();
  }

  onAudit(listener: AuditListener): () => void {
    this.auditListeners.push(listener);
    return () => {
      const i = this.auditListeners.indexOf(listener);
      if (i >= 0) this.auditListeners.splice(i, 1);
    };
  }

  /** Convenience: get a workspace by id, or undefined. */
  getWorkspace(id: WorkspaceId): Workspace | undefined {
    return this.workspaces.get(id);
  }

  /** Serialise all workspaces to a JSON-safe structure for persistence. */
  snapshot(): unknown {
    const out: Array<Record<string, unknown>> = [];
    for (const ws of this.workspaces.values()) {
      out.push({
        id:           ws.id,
        created:      ws.created,
        state:        ws.state,
        profiles:     ws.profiles,
        mode:         ws.mode,
        mode_ceiling: ws.mode_ceiling,
        routing_policy_uri: ws.routing_policy_uri,
        step_up_window_sec: ws.step_up_window_sec,
        members:         Array.from(ws.members.values()),
        tasks:           Array.from(ws.tasks.values()),
        overrides:       Array.from(ws.overrides.values()),
        whispers:        Array.from(ws.whispers.values()),
        deliberations:   Array.from(ws.deliberations.values()),
        handoffs:        Array.from(ws.handoffs.values()),
        snapshots:       Array.from(ws.snapshots.values()),
        route_decisions: Array.from(ws.route_decisions.values()),
        audit:           ws.audit,
        chain_head:      ws.chain_head,
        chain_enabled:   ws.chain_enabled,
      });
    }
    return out;
  }

  /** Restore from a snapshot produced by ``snapshot()``. Replaces
   *  any existing in-memory workspaces. */
  restore(data: unknown): void {
    this.workspaces.clear();
    if (!Array.isArray(data)) return;
    for (const w of data as Array<Record<string, unknown>>) {
      const ws: Workspace = {
        id: w.id as WorkspaceId,
        created: w.created as string,
        state: (w.state as Workspace["state"]) ?? "active",
        profiles: (w.profiles as string[]) ?? [],
        mode: (w.mode as Mode) ?? "trial",
        mode_ceiling: (w.mode_ceiling as Mode) ?? "production",
        routing_policy_uri: w.routing_policy_uri as string | undefined,
        step_up_window_sec: (w.step_up_window_sec as number) ?? 300,
        members: new Map(),
        tasks: new Map(),
        overrides: new Map(),
        whispers: new Map(),
        deliberations: new Map(),
        handoffs: new Map(),
        snapshots: new Map(),
        route_decisions: new Map(),
        audit: (w.audit as AuditEntry[]) ?? [],
        chain_head: w.chain_head as string | undefined,
        chain_enabled: !!w.chain_enabled,
      };
      for (const m of (w.members as Member[]) ?? []) {
        // Defensive: ensure required collections exist even on older snapshots.
        if (!Array.isArray(m.keys)) m.keys = [];
        if (m.paused === undefined) m.paused = false;
        ws.members.set(m.uri, m);
      }
      for (const t of (w.tasks as Task[]) ?? []) ws.tasks.set(t.id, t);
      for (const o of (w.overrides as OverrideArtefact[]) ?? []) ws.overrides.set(o.id, o);
      // The dynamic profile collections may not be present on legacy snapshots.
      for (const x of (w.whispers as Array<{ id: string } & Record<string, unknown>>) ?? [])
        ws.whispers.set(x.id, x as never);
      for (const x of (w.deliberations as Array<{ id: string } & Record<string, unknown>>) ?? [])
        ws.deliberations.set(x.id, x as never);
      for (const x of (w.handoffs as Array<{ id: string } & Record<string, unknown>>) ?? [])
        ws.handoffs.set(x.id, x as never);
      for (const x of (w.snapshots as Array<{ id: string } & Record<string, unknown>>) ?? [])
        ws.snapshots.set(x.id, x as never);
      for (const x of (w.route_decisions as Array<{ id: string } & Record<string, unknown>>) ?? [])
        ws.route_decisions.set(x.id, x as never);
      this.workspaces.set(ws.id, ws);
    }
  }

  // -- dispatch ------------------------------------------------------

  dispatch(envelope: Envelope): Envelope {
    if (!isValidEnvelope(envelope) || !envelope.method) {
      return reply(envelope, { error: rpcError(E.REQUEST, "Invalid JSON-RPC 2.0 request") });
    }
    const method = envelope.method;
    const params = (envelope.params ?? {}) as Record<string, unknown>;

    // security-signed/1.0: verify top-level sig if required.
    if (this.options.requireSignatures && method !== "participant.join") {
      const sigErr = this.verifySignature(envelope);
      if (sigErr) return reply(envelope, { error: sigErr });
    }

    // identity-oidc/1.0: step-up freshness on privileged methods.
    if (this.options.enforceStepUp && PRIVILEGED_METHODS.has(method)) {
      const stale = this.checkStepUp(params);
      if (stale) return reply(envelope, { error: stale });
    }

    // control/1.0 workspace-paused gate (S6 -32063).
    const exempt = new Set(["workspace.create", "workspace.describe",
                            "control.resume", "audit.read",
                            "participant.join", "participant.leave"]);
    if (!exempt.has(method)) {
      const wsId = params.workspace as string | undefined;
      if (typeof wsId === "string") {
        const ws = this.workspaces.get(wsId);
        if (ws && ws.state === "paused") {
          return reply(envelope, { error: rpcError(E.CONTROL_WORKSPACE_PAUSED, `Workspace ${wsId} is paused`) });
        }
      }
    }

    const handler = this.handlers.get(method);
    if (!handler) {
      return reply(envelope, { error: rpcError(E.METHOD, `Unknown method: ${method}`) });
    }

    let out: { result?: unknown; error?: { code: number; message: string; data?: unknown } };
    try {
      out = handler(params);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return reply(envelope, { error: rpcError(E.INTERNAL, `Internal error: ${msg}`) });
    }
    if (out.error) return reply(envelope, { error: out.error });

    // Record audit on success
    const wsId = params.workspace as string | undefined;
    if (typeof wsId === "string") {
      const ws = this.workspaces.get(wsId);
      if (ws) this.recordAudit(ws, envelope);
    }
    return reply(envelope, { result: out.result });
  }

  // -- audit ---------------------------------------------------------

  recordAudit(ws: Workspace, envelope: Envelope): void {
    const entry: AuditEntry = {
      seq: ws.audit.length,
      arrived: this.now(),
      envelope: JSON.parse(JSON.stringify(envelope)) as Envelope,
    };
    if (ws.chain_enabled || this.options.enableChain) {
      const prev = ws.chain_head ?? ZERO_HASH;
      entry.prev_hash = prev;
      ws.chain_head = linkHash(entry.envelope, prev);
    }
    ws.audit.push(entry);
    for (const l of this.auditListeners) {
      try { l(ws, entry); } catch { /* listeners never break dispatch */ }
    }
    // Persistence: only meaningful when a non-Memory store is configured;
    // MemoryStore.save() is a no-op for the dispatcher's perspective.
    this.persist(ws);
  }

  // -- signature verification (security-signed/1.0) -----------------

  private verifySignature(envelope: Envelope): { code: number; message: string; data?: unknown } | null {
    const sig = envelope.sig;
    if (!sig || typeof sig !== "string") {
      return rpcError(E.SIG_VERIFY_FAILED, "Missing top-level `sig` field");
    }
    const parts = sig.split(":");
    if (parts.length !== 3 || parts[0] !== "ed25519") {
      return rpcError(E.SIG_VERIFY_FAILED, "Malformed signature tag");
    }
    const kid = parts[1];
    const params = (envelope.params ?? {}) as Record<string, unknown>;
    const sender = params.from as string | undefined;
    const wsId = params.workspace as string | undefined;
    const ts = (params.ts as string | undefined) ?? this.now();
    if (!sender || !wsId) return null;  // not enough context; let handler reject
    const ws = this.workspaces.get(wsId);
    if (!ws) return null;
    const member = ws.members.get(sender);
    if (!member) return rpcError(E.SIG_KEY_NOT_FOUND, `No member: ${sender}`);

    const key = this.lookupKey(member, kid, ts);
    if (!key) {
      const revoked = member.keys.find(k => k.kid === kid && k.revoked_at);
      if (revoked) return rpcError(E.SIG_KEY_REVOKED, `Key ${kid} is revoked`);
      return rpcError(E.SIG_KEY_NOT_FOUND, `No key ${kid} valid at ${ts} for ${sender}`);
    }

    // Verify
    try {
      const stripped: Envelope = JSON.parse(JSON.stringify(envelope));
      delete stripped.sig;
      const canonical = canonicalize(stripped);
      const pub = publicKeyFromJwk(key.jwk);
      if (!verifyEnvelope(canonical, sig, pub)) {
        return rpcError(E.SIG_VERIFY_FAILED, "Signature failed verification");
      }
      return null;
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return rpcError(E.INTERNAL, `Signature check error: ${msg}`);
    }
  }

  /** Lookup the key valid for a given participant at a given time. */
  lookupKey(member: Member, kid: string, ts: string): KeyRecord | null {
    for (const k of member.keys) {
      if (k.kid !== kid) continue;
      if (k.revoked_at !== undefined && ts >= k.revoked_at) continue;
      if (ts < k.valid_from) continue;
      if (k.valid_until !== undefined && ts >= k.valid_until) continue;
      return k;
    }
    return null;
  }

  // -- step-up (identity-oidc/1.0) -----------------------------------

  private checkStepUp(params: Record<string, unknown>): { code: number; message: string; data?: unknown } | null {
    const wsId = params.workspace as string | undefined;
    const sender = params.from as string | undefined;
    if (!wsId || !sender) return null;
    const ws = this.workspaces.get(wsId);
    if (!ws) return null;
    const member = ws.members.get(sender);
    if (!member || member.oidc_auth_time === undefined) return null;
    const nowSec = Math.floor(Date.now() / 1000);
    const age = nowSec - member.oidc_auth_time;
    if (age > ws.step_up_window_sec) {
      return rpcError(E.OIDC_STEP_UP_REQUIRED, "Step-up authentication required", {
        window_sec: ws.step_up_window_sec, age_sec: age,
      });
    }
    return null;
  }

  // ==========================================================
  //   Registration
  // ==========================================================

  private registerCoreHandlers(): void {
    this.handlers.set("workspace.create",       p => this.opWorkspaceCreate(p));
    this.handlers.set("workspace.describe",     p => this.opWorkspaceDescribe(p));
    this.handlers.set("workspace.set_profiles", p => this.opWorkspaceSetProfiles(p));
    this.handlers.set("participant.join",       p => this.opParticipantJoin(p));
    this.handlers.set("participant.leave",      p => this.opParticipantLeave(p));
    this.handlers.set("task.create",            p => this.opTaskCreate(p));
    this.handlers.set("task.update",            p => this.opTaskUpdate(p));
    this.handlers.set("task.complete",          p => this.opTaskComplete(p));
    this.handlers.set("audit.read",             p => this.opAuditRead(p));
    this.handlers.set("review.request",         p => this.opReviewRequest(p));
    this.handlers.set("decide.approve",         p => this.opDecide(p, "approve"));
    this.handlers.set("decide.reject",          p => this.opDecide(p, "reject"));
    this.handlers.set("decide.override",        p => this.opDecideOverride(p));
    this.handlers.set("abstain.declare",        p => this.opAbstainDeclare(p));
    this.handlers.set("escalate.raise",         p => this.opEscalateRaise(p));
  }

  private registerProfileHandlers(): void {
    // Profiles register themselves by mutating this.handlers
    registerWhisper(this);
    registerDeliberation(this);
    registerHandoff(this);
    registerControl(this);
    registerRouting(this);
    registerSecuritySigned(this);
    registerAuditScitt(this);
  }

  // ==========================================================
  //   Core method handlers
  // ==========================================================

  private opWorkspaceCreate(p: Record<string, unknown>): ReturnType<Handler> {
    const id = (p.workspace as string) || this.ids.workspaceId();
    if (typeof id !== "string") return { error: rpcError(E.PARAMS, "workspace must be a string id") };
    if (this.workspaces.has(id)) return { error: rpcError(E.PARAMS, `workspace already exists: ${id}`) };
    const profiles: string[] = Array.isArray(p.profiles) ? [...(p.profiles as string[])] : [...(this.options.defaultProfiles ?? ["core/1.0", "review/1.0"])];
    const chainEnabled = profiles.includes("audit-scitt/1.0") || !!this.options.enableChain;
    const ws: Workspace = {
      id,
      created: this.now(),
      state: "active",
      profiles,
      mode: (p.mode as Mode) ?? "trial",
      mode_ceiling: (p.mode_ceiling as Mode) ?? "production",
      routing_policy_uri: p.routing_policy_uri as string | undefined,
      step_up_window_sec: typeof p.step_up_window_sec === "number" ? p.step_up_window_sec : 300,
      members: new Map(),
      tasks: new Map(),
      overrides: new Map(),
      whispers: new Map(),
      deliberations: new Map(),
      handoffs: new Map(),
      snapshots: new Map(),
      route_decisions: new Map(),
      audit: [],
      chain_enabled: chainEnabled,
      chain_head: chainEnabled ? ZERO_HASH : undefined,
    };
    this.workspaces.set(id, ws);
    return { result: { workspace: id, created: ws.created } };
  }

  private opWorkspaceDescribe(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, `Unknown workspace: ${p.workspace}`) };
    return { result: {
      id: ws.id,
      created: ws.created,
      state: ws.state,
      mode: ws.mode,
      mode_ceiling: ws.mode_ceiling,
      step_up_window_sec: ws.step_up_window_sec,
      profiles: ws.profiles,
      members: Array.from(ws.members.values()).map(memberToDict),
      audit_count: ws.audit.length,
      task_count: ws.tasks.size,
      override_count: ws.overrides.size,
      evidence_head: ws.chain_head,
      ...(ws.routing_policy_uri ? { routing_policy_uri: ws.routing_policy_uri } : {}),
    }};
  }

  private opWorkspaceSetProfiles(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "profiles"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const newProfiles = [...(p.profiles as string[])];
    if (!newProfiles.some(pr => pr.startsWith("core/"))) newProfiles.push("core/1.0");
    ws.profiles = newProfiles;
    if (newProfiles.includes("audit-scitt/1.0") && !ws.chain_enabled) {
      ws.chain_enabled = true;
      ws.chain_head = ZERO_HASH;
    }
    return { result: { profiles: ws.profiles } };
  }

  private opParticipantJoin(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "from", "type"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    let ws = this.workspaces.get(p.workspace as string);
    if (!ws) {
      const create = this.opWorkspaceCreate({ workspace: p.workspace });
      if (create.error) return create;
      ws = this.workspaces.get(p.workspace as string)!;
    }
    const uri = p.from as ParticipantUri;
    const now = this.now();

    const member: Member = {
      uri,
      type: p.type as Member["type"],
      role: (p.role as string) || "participant",
      joined: now,
      display_name: p.display_name as string | undefined,
      capabilities: p.capabilities as Record<string, unknown> | undefined,
      scopes: p.scopes as string[] | undefined,
      keys: [],
      paused: false,
    };

    // identity-oidc/1.0
    if (this.options.verifyOidcToken && typeof p.oidc_token === "string") {
      const claims = this.options.verifyOidcToken(p.oidc_token);
      if (claims === null) return { error: rpcError(E.OIDC_TOKEN_INVALID, "OIDC token invalid") };
      member.oidc_sub = claims.sub as string | undefined;
      const at = claims.auth_time;
      if (typeof at === "number") member.oidc_auth_time = at;
      const cnf = claims.cnf as Record<string, unknown> | undefined;
      const cnfJwk = cnf?.jwk as Record<string, unknown> | undefined;
      if (cnfJwk && typeof cnfJwk.kid === "string") {
        member.keys.push({ jwk: cnfJwk as unknown as KeyRecord["jwk"], kid: cnfJwk.kid as string, valid_from: now });
      }
    }
    // identity-vc/1.0
    if (this.options.verifyVc && typeof p.vc_presentation === "object" && p.vc_presentation !== null) {
      const subject = this.options.verifyVc(p.vc_presentation as Record<string, unknown>);
      if (subject === null) return { error: rpcError(E.VC_VP_INVALID, "VC presentation invalid") };
      member.vc_holder = (subject.holder as string | undefined) ?? (subject.id as string | undefined);
      const vpJwk = subject.cnf_jwk as Record<string, unknown> | undefined;
      if (vpJwk && typeof vpJwk.kid === "string") {
        member.keys.push({ jwk: vpJwk as unknown as KeyRecord["jwk"], kid: vpJwk.kid as string, valid_from: now });
      }
    }

    // security-signed/1.0: register supplied JWKs
    const jwks = p.jwks as { keys?: Array<{ kid: string; [k: string]: unknown }> } | undefined;
    if (jwks?.keys) {
      for (const j of jwks.keys) {
        if (j.kid && !member.keys.some(k => k.kid === j.kid)) {
          member.keys.push({ jwk: j as unknown as KeyRecord["jwk"], kid: j.kid, valid_from: now });
        }
      }
    }

    ws.members.set(uri, member);
    return { result: { joined: true, as: uri } };
  }

  private opParticipantLeave(p: Record<string, unknown>): ReturnType<Handler> {
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    ws.members.delete(p.from as string);
    return { result: { left: true } };
  }

  private opTaskCreate(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "from", "kind", "input"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const assignee = (p.assignee as string) || (p.to as string);
    if (!assignee || !ws.members.has(assignee)) {
      return { error: rpcError(E.PARAMS, "Assignee not in workspace") };
    }
    if (ws.members.get(assignee)!.paused) {
      return { error: rpcError(E.CONTROL_WORKSPACE_PAUSED, `Assignee ${assignee} is paused`) };
    }

    // modes/1.0 ceiling check
    const requestedMode = ((p.mode as Mode) || ws.mode);
    if (!modeLE(requestedMode, ws.mode_ceiling)) {
      return { error: rpcError(E.MODE_CEILING_EXCEEDED,
        `Requested mode ${requestedMode} exceeds ceiling ${ws.mode_ceiling}`) };
    }

    const taskId = this.ids.taskId();
    const now = this.now();
    const task: Task = {
      id: taskId,
      kind: p.kind as string,
      state: "created",
      assignee,
      delegator: p.from as ParticipantUri,
      input: p.input as Record<string, unknown>,
      created_at: now,
      updated_at: now,
      deadline: p.deadline as string | undefined,
      mode: requestedMode,
      routing_hints: p.routing_hints as Task["routing_hints"],
      history: [{ ts: now, from: p.from as ParticipantUri, state: "created" }],
      paused: false,
    };

    // modes/1.0: trial mode forces review
    if (task.mode === "trial") task.review_required = true;
    else if ("review_required" in p) task.review_required = !!p.review_required;

    ws.tasks.set(taskId, task);
    return { result: { task_id: taskId, state: "created" } };
  }

  private opTaskUpdate(p: Record<string, unknown>): ReturnType<Handler> {
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    const newState = p.state as Task["state"];
    const legal: Record<string, string[]> = {
      created:          ["in_progress", "declined", "paused"],
      in_progress:      ["in_progress", "completed", "declined", "review_requested", "paused"],
      review_requested: ["in_progress", "completed", "declined"],
      paused:           ["in_progress", "cancelled"],
    };
    if (!legal[task.state]?.includes(newState)) {
      return { error: rpcError(E.PARAMS, `Illegal transition ${task.state} -> ${newState}`) };
    }
    task.state = newState;
    task.updated_at = this.now();
    task.history.push({
      ts: task.updated_at, from: p.from as ParticipantUri,
      state: newState, note: p.progress_note as string | undefined,
    });
    return { result: { state: newState } };
  }

  private opTaskComplete(p: Record<string, unknown>): ReturnType<Handler> {
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    if (task.state === "completed" || task.state === "declined") {
      return { error: rpcError(E.PARAMS, `Task is terminal: ${task.state}`) };
    }
    task.output = p.output;
    if (typeof p.confidence === "number") task.confidence = p.confidence;
    task.state = "completed";
    task.updated_at = this.now();
    task.history.push({ ts: task.updated_at, from: p.from as ParticipantUri, state: "completed" });
    return { result: { state: "completed" } };
  }

  private opAuditRead(p: Record<string, unknown>): ReturnType<Handler> {
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const range = (p.range as { from_seq?: number; to_seq?: number } | undefined) ?? {};
    const filter = (p.filter as { method?: string; from?: string; task_id?: string } | undefined) ?? {};
    const fromSeq = range.from_seq ?? 0;
    const toSeq = range.to_seq ?? ws.audit.length;
    const out: unknown[] = [];
    for (const entry of ws.audit.slice(fromSeq, toSeq)) {
      const ep = (entry.envelope.params ?? {}) as Record<string, unknown>;
      if (filter.method && entry.envelope.method !== filter.method) continue;
      if (filter.from && ep.from !== filter.from) continue;
      if (filter.task_id && ep.task_id !== filter.task_id) continue;
      const item: Record<string, unknown> = {
        seq: entry.seq, arrived: entry.arrived, envelope: entry.envelope,
      };
      if (entry.prev_hash) item.prev_hash = entry.prev_hash;
      out.push(item);
    }
    return { result: { entries: out, next_seq: toSeq } };
  }

  // ==========================================================
  //   review/1.0 handlers
  // ==========================================================

  private opReviewRequest(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "from", "task_id", "artefact"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    const to = p.to;
    const reviewers: string[] = Array.isArray(to) ? (to as string[]) : typeof to === "string" ? [to] : [];
    if (!reviewers.length) return { error: rpcError(E.PARAMS, "review.request needs 'to'") };
    const now = this.now();
    task.state = "review_requested";
    task.updated_at = now;
    task.review = {
      requested_at: now,
      requested_to: reviewers,
      rule: (p.rule as string) || "any_one_approves",
      deadline: p.deadline as string | undefined,
      decisions: [],
    };
    task.history.push({ ts: now, from: p.from as ParticipantUri, state: "review_requested" });
    task.pending_artefact = p.artefact;
    return { result: { state: "review_requested", review_id: task.id } };
  }

  private opDecide(p: Record<string, unknown>, kind: "approve" | "reject"): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "from", "task_id"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    if (task.state !== "review_requested") {
      return { error: rpcError(E.NOT_REVIEWABLE, `Task not awaiting review: ${task.state}`) };
    }
    const now = this.now();
    task.review!.decisions.push({
      reviewer: p.from as ParticipantUri,
      kind,
      ts: now,
      comment: p.comment as string | undefined,
      tags: p.tags as string[] | undefined,
    });
    if (kind === "approve") {
      task.output = task.pending_artefact;
      task.state = "completed";
    } else {
      task.state = p.request_revision ? "in_progress" : "declined";
    }
    task.updated_at = now;
    task.history.push({ ts: now, from: p.from as ParticipantUri, state: task.state });
    return { result: { state: task.state } };
  }

  private opDecideOverride(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "from", "task_id", "diff", "rationale"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    if (task.state !== "review_requested") {
      return { error: rpcError(E.NOT_REVIEWABLE, `Task not awaiting review: ${task.state}`) };
    }
    const base = p.based_on_artefact !== undefined ? p.based_on_artefact : task.pending_artefact;
    if (base === undefined) return { error: rpcError(E.PARAMS, "No base artefact for override") };
    let applied: unknown;
    try {
      applied = applyJsonPatch(base, p.diff as never);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      return { error: rpcError(E.PATCH_FAILED, msg) };
    }
    const now = this.now();
    const id = this.ids.artefactId();
    const override: OverrideArtefact = {
      id,
      task_id: task.id,
      reviewer: p.from as ParticipantUri,
      based_on_artefact: base,
      diff: p.diff as never,
      result: applied,
      rationale: p.rationale as string,
      tags: (p.tags as string[]) ?? [],
      policy_refs: (p.policy_refs as string[]) ?? [],
      ts: now,
      logical_id: p.logical_id as string | undefined,
      instance_id: p.instance_id as string | undefined,
      intent_preserved: p.intent_preserved as boolean | undefined,
    };
    ws.overrides.set(id, override);
    task.review!.decisions.push({
      reviewer: p.from as ParticipantUri,
      kind: "override",
      ts: now,
      tags: override.tags,
      override_artefact_id: id,
    });
    task.output = applied;
    task.state = "completed";
    task.updated_at = now;
    task.history.push({ ts: now, from: p.from as ParticipantUri, state: "completed", note: "override applied" });
    return { result: { state: "completed", override_artefact_id: id, applied } };
  }

  private opAbstainDeclare(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "from", "task_id", "reason"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const task = ws.tasks.get(p.task_id as string);
    if (!task) return { error: rpcError(E.PARAMS, "Unknown task") };
    if (task.state !== "review_requested") {
      return { error: rpcError(E.NOT_REVIEWABLE, `Task not awaiting review: ${task.state}`) };
    }
    const now = this.now();
    task.review!.decisions.push({
      reviewer: p.from as ParticipantUri,
      kind: "abstain",
      ts: now,
      comment: p.reason as string,
      abstain_category: (p.category as string) || "other",
    });
    task.state = "abstained";
    task.updated_at = now;
    task.history.push({ ts: now, from: p.from as ParticipantUri, state: "abstained", note: p.reason as string });
    return { result: { state: "abstained" } };
  }

  private opEscalateRaise(p: Record<string, unknown>): ReturnType<Handler> {
    const miss = missing(p, ["workspace", "from", "original_task_id", "new_task"]);
    if (miss) return { error: rpcError(E.PARAMS, `Missing field: ${miss}`) };
    const ws = this.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const orig = ws.tasks.get(p.original_task_id as string);
    if (!orig) return { error: rpcError(E.PARAMS, "Unknown original task") };
    const nt = p.new_task as Record<string, unknown>;
    const assignee = nt.assignee as string;
    if (!assignee || !ws.members.has(assignee)) {
      return { error: rpcError(E.PARAMS, "Escalation assignee not in workspace") };
    }
    const now = this.now();
    const newId = this.ids.taskId();
    const newTask: Task = {
      id: newId,
      kind: (nt.kind as string) || orig.kind,
      state: "created",
      assignee,
      delegator: p.from as ParticipantUri,
      input: (nt.input as Record<string, unknown>) ?? {},
      created_at: now,
      updated_at: now,
      mode: orig.mode,
      supersedes: orig.id,
      history: [{ ts: now, from: p.from as ParticipantUri, state: "created", note: `escalated from ${orig.id}` }],
      paused: false,
    };
    ws.tasks.set(newId, newTask);
    orig.state = "escalated";
    orig.updated_at = now;
    orig.superseded_by = newId;
    orig.history.push({ ts: now, from: p.from as ParticipantUri, state: "escalated", note: `-> ${newId}` });
    return { result: { new_task_id: newId, escalated_from: orig.id } };
  }
}

function memberToDict(m: Member): Record<string, unknown> {
  const out: Record<string, unknown> = {
    uri: m.uri, type: m.type, role: m.role, joined: m.joined,
  };
  if (m.display_name) out.display_name = m.display_name;
  if (m.capabilities) out.capabilities = m.capabilities;
  if (m.scopes) out.scopes = m.scopes;
  if (m.keys.length) {
    out.jwks = { keys: m.keys.filter(k => !k.revoked_at && !k.valid_until).map(k => k.jwk) };
    out.key_history = m.keys;
  }
  if (m.paused) out.paused = true;
  if (m.oidc_sub) out.oidc_sub = m.oidc_sub;
  if (m.vc_holder) out.vc_holder = m.vc_holder;
  return out;
}
