import { Coordinator } from "../../packages/coordinator/src/index.ts";
import type { Store, StoreChange, WorkspaceRecord } from "../../packages/coordinator/src/storage/store.ts";

/** Objects with their keys sorted, so two snapshots compare by content. */
function sorted(x: unknown): unknown {
  if (Array.isArray(x)) return x.map(sorted);
  if (x && typeof x === "object") {
    return Object.fromEntries(Object.keys(x).sort().map(k => [k, sorted((x as Record<string, unknown>)[k])]));
  }
  return x;
}

type Item = Record<string, unknown>;
const isItem = (x: unknown): x is Item => !!x && typeof x === "object" && !Array.isArray(x);

/**
 * The fields of the coordinators' own records whose null is a value, as in
 * fuzz.py: a whisper's default_if_lapsed is the default a lapse applies.
 */
const KEPT_NULLS: Record<string, string[]> = { whispers: ["default_if_lapsed"] };
/** The records nested in each collection's objects, by field, as in fuzz.py. */
const NESTED: Record<string, string[]> = {
  members: ["keys"], tasks: ["history"], deliberations: ["votes"], handoffs: ["tasks"],
};

/**
 * A record of a coordinator's own, with the fields holding null left out but
 * those named in `keep`, as fuzz.py's _own.
 */
function own(record: Item, keep: string[] = []): Item {
  return Object.fromEntries(Object.entries(record).filter(([k, v]) => v !== null || keep.includes(k)));
}

/**
 * One object of a collection with its own nulls left out, and those of the
 * records nested in it, as fuzz.py's _shape. What a client sent, and the log,
 * keep their nulls.
 */
function shape(name: string, item: Item): Item {
  const out = own(item, KEPT_NULLS[name]);
  for (const field of NESTED[name] ?? []) {
    if (Array.isArray(out[field])) out[field] = (out[field] as unknown[]).map((x) => (isItem(x) ? own(x) : x));
  }
  if (name === "tasks" && isItem(out.review)) {
    const review = own(out.review);
    if (Array.isArray(review.decisions)) {
      review.decisions = (review.decisions as unknown[]).map((d) => (isItem(d) ? own(d) : d));
    }
    out.review = review;
  }
  return out;
}

const byKey = (a: Item, b: Item) => {
  const x = String(a.id ?? a.uri), y = String(b.id ?? b.uri);
  return x < y ? -1 : x > y ? 1 : 0;
};
const values = (m: Map<string, unknown>) =>
  JSON.parse(JSON.stringify([...Map.prototype.values.call(m)])) as Item[];

/** Every workspace's state, by id, as fuzz.py's project() gives the Python one. */
function project(coord: Coordinator): unknown {
  const ids = [...coord.workspaces.keys()].sort();
  return Object.fromEntries(ids.map((id) => [id, projectWorkspace(coord, id)]));
}

/** One workspace's state in the shape fuzz.py gives the Python workspace. */
function projectWorkspace(coord: Coordinator, workspace: string): unknown {
  const ws = coord.workspaces.get(workspace);
  if (!ws) return null;
  const out: Item = own(JSON.parse(JSON.stringify({
    id: ws.id, created: ws.created, state: ws.state, profiles: ws.profiles,
    mode: ws.mode, mode_ceiling: ws.mode_ceiling,
    routing_policy_uri: ws.routing_policy_uri, step_up_window_sec: ws.step_up_window_sec,
    min_acr: ws.min_acr, signature_policy: ws.signature_policy, join_policy: ws.join_policy,
    review_rule: ws.review_rule, chain_head: ws.chain_head, chain_enabled: ws.chain_enabled,
  })));
  const collection = (name: string, m: Map<string, unknown>, f?: (x: Item) => Item) =>
    values(m).map((x) => shape(name, f ? f(x) : x)).sort(byKey);
  out.members = collection("members", ws.members);
  out.tasks = collection("tasks", ws.tasks, (t) => {
    const review = t.review as Item | undefined;
    if (review && Array.isArray(review.decisions)) {
      for (const d of review.decisions as Item[]) {
        if (d && typeof d === "object" && Array.isArray(d.tags) && d.tags.length === 0) delete d.tags;
      }
    }
    return t;
  });
  out.overrides = collection("overrides", ws.overrides);
  out.whispers = collection("whispers", ws.whispers);
  out.deliberations = collection("deliberations", ws.deliberations);
  out.snapshots = collection("snapshots", ws.snapshots);
  out.invitations = collection("invitations", ws.invitations);
  out.handoffs = collection("handoffs", ws.handoffs, (h) => {
    if (Array.isArray(h.accepted_task_ids) && h.accepted_task_ids.length === 0) delete h.accepted_task_ids;
    return h;
  });
  out.route_decisions = collection("route_decisions", ws.route_decisions);
  out.idempotency_keys = { ...ws.idempotency_keys };
  out.audit = JSON.parse(JSON.stringify(ws.audit));
  return out;
}

/** The fuzzer's token verifier, as fuzz.py's stub_token_verifier. */
function stubTokenVerifier(token: string): Record<string, unknown> | null {
  if (typeof token !== "string" || !token.startsWith("stub.")) return null;
  try {
    const claims = JSON.parse(token.slice("stub.".length));
    return isItem(claims) ? claims : null;
  } catch {
    return null;
  }
}

/** The fuzzer's credential verifier, as fuzz.py's stub_credential_verifier. */
function stubCredentialVerifier(presentation: Record<string, unknown>): Record<string, unknown> | null {
  const subject = isItem(presentation) ? presentation.subject : undefined;
  return isItem(subject) ? subject : null;
}

interface Step { writer: number; write: boolean; reload: boolean; version: boolean; race: boolean }

/**
 * A SQLite store that fails the reads and writes it is told to fail, and runs
 * a hook once before its next write, as fuzz.py's _RoughStore.
 */
class RoughStore implements Store {
  failWrite = false;
  failReload = false;
  failVersion = false;
  beforeWrite: (() => void) | undefined;
  constructor(private readonly inner: Store & Required<Pick<Store, "apply" | "version" | "loadOne">>) {}
  load() { return this.inner.load(); }
  loadOne(id: string) {
    if (this.failReload) throw new Error("the fuzzer failed this reload");
    return this.inner.loadOne(id);
  }
  version(id: string) {
    if (this.failVersion) throw new Error("the fuzzer failed this read");
    return this.inner.version(id);
  }
  apply(change: StoreChange) {
    const hook = this.beforeWrite;
    this.beforeWrite = undefined;
    hook?.();
    if (this.failWrite) throw new Error("the fuzzer failed this write");
    return this.inner.apply(change);
  }
  save(record: WorkspaceRecord) { return this.inner.save(record); }
  delete(id: string) { return this.inner.delete(id); }
  close() { return this.inner.close(); }
}

async function main(): Promise<void> {
  const chunks: Buffer[] = [];
  for await (const c of process.stdin) chunks.push(c as Buffer);
  const req = JSON.parse(Buffer.concat(chunks).toString("utf-8")) as {
    options: { defaultProfiles: string[]; stubVerifiers: boolean; readMembership: boolean };
    workspace: string;
    envelopes: unknown[];
    store?: string;
    lapse_at?: string;
    /** A store the Python coordinator wrote, to project as this one reads it. */
    cross?: string;
    /** Two writers on `store` and the faults it meets, as fuzz.py's _rough_py. */
    rough?: Step[];
  };
  const options = {
    deterministicIds: true,
    deterministicClock: true,
    enableChain: true,
    defaultProfiles: req.options.defaultProfiles,
    ...(req.options.stubVerifiers
      ? { verifyOidcToken: stubTokenVerifier, verifyVc: stubCredentialVerifier, enforceStepUp: true }
      : {}),
    ...(req.options.readMembership ? { requireReadMembership: true } : {}),
  };
  const copy = (e: unknown) => JSON.parse(JSON.stringify(e));
  if (!req.store) {
    const coord = new Coordinator(options);
    const responses: unknown[] = [];
    const states: unknown[] = [];
    for (const e of req.envelopes) {
      responses.push(coord.dispatch(copy(e)));
      states.push(project(coord));
    }
    const chain_head = coord.workspaces.get(req.workspace)?.chain_head ?? null;
    process.stdout.write(JSON.stringify({ responses, states, chain_head }));
    return;
  }
  const { SqliteStore } = await import("../../packages/coordinator/src/storage/sqlite.ts");
  if (req.rough) {
    const schedule = req.rough;
    const stores = [0, 1].map(() => new RoughStore(new SqliteStore(req.store!)));
    const coords = stores.map((s) => new Coordinator({ ...options, store: s, onStoreError: () => undefined }));
    const responses: unknown[] = new Array(req.envelopes.length).fill(null);
    for (let i = 0; i < req.envelopes.length;) {
      const step = schedule[i];
      const store = stores[step.writer];
      let raced = false;
      if (step.race) {
        store.beforeWrite = () => {
          responses[i + 1] = coords[1 - step.writer].dispatch(copy(req.envelopes[i + 1]));
          raced = true;
        };
      }
      store.failWrite = step.write;
      store.failReload = step.reload;
      store.failVersion = step.version;
      responses[i] = coords[step.writer].dispatch(copy(req.envelopes[i]));
      store.failWrite = store.failReload = store.failVersion = false;
      store.beforeWrite = undefined;
      i += raced ? 2 : 1;
    }
    for (const s of stores) s.close();
    process.stdout.write(JSON.stringify({ responses }));
    return;
  }
  // The same calls on a SQLite file: every write must have reached it, so a
  // coordinator restarted on the file holds what this one holds.
  const errors: string[] = [];
  const open = () => {
    const s = new SqliteStore(req.store!);
    return { s, c: new Coordinator({ ...options, store: s, onStoreError: (e) => { errors.push(String(e)); } }) };
  };
  const held = (c: Coordinator) => JSON.stringify(sorted(JSON.parse(JSON.stringify(c.snapshot()))));
  let { s: store, c: coord } = open();
  const responses = req.envelopes.map((e) => coord.dispatch(copy(e)));
  let restartSame = true;
  // The whispers lapse on the restarted coordinator, so a default it lost on
  // the way shows up in the entries the lapses write.
  for (const lapse of [true, false]) {
    const before = held(coord);
    store.close();
    ({ s: store, c: coord } = open());
    restartSame &&= before === held(coord);
    if (lapse && req.lapse_at) coord.checkWhisperLapses?.(req.workspace, req.lapse_at);
  }
  const own = project(coord);
  store.close();
  // Each coordinator reads the other's file: projected, it holds what the
  // file this one wrote holds.
  let cross: unknown = null;
  if (req.cross) {
    const other = new SqliteStore(req.cross);
    cross = project(new Coordinator({ ...options, store: other }));
    other.close();
  }
  process.stdout.write(JSON.stringify({ responses, errors, restart_same: restartSame, own, cross }));
}

main().catch((e) => {
  process.stderr.write(String(e?.stack ?? e) + "\n");
  process.exit(1);
});
