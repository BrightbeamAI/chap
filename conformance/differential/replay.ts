import { Coordinator } from "../../packages/coordinator/src/index.ts";

/** Objects with their keys sorted, so two snapshots compare by content. */
function sorted(x: unknown): unknown {
  if (Array.isArray(x)) return x.map(sorted);
  if (x && typeof x === "object") {
    return Object.fromEntries(Object.keys(x).sort().map(k => [k, sorted((x as Record<string, unknown>)[k])]));
  }
  return x;
}

/** A value as JSON holds it once nulls are left out, as fuzz.py's _drop_null. */
function dropNull(x: unknown): unknown {
  if (Array.isArray(x)) return x.map(dropNull);
  if (x && typeof x === "object") {
    return Object.fromEntries(Object.entries(x)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) => [k, dropNull(v)]));
  }
  return x;
}

type Item = Record<string, unknown>;
const byId = (key: string) => (a: Item, b: Item) => {
  const x = String(a[key]), y = String(b[key]);
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
  const out: Item = {
    id: ws.id, created: ws.created, state: ws.state, profiles: ws.profiles,
    mode: ws.mode, mode_ceiling: ws.mode_ceiling,
    routing_policy_uri: ws.routing_policy_uri, step_up_window_sec: ws.step_up_window_sec,
    min_acr: ws.min_acr, chain_head: ws.chain_head, chain_enabled: ws.chain_enabled,
  };
  out.members = values(ws.members).sort(byId("uri"));
  out.tasks = values(ws.tasks).map((t) => {
    const review = t.review as Item | undefined;
    if (review && Array.isArray(review.decisions)) {
      for (const d of review.decisions as Item[]) {
        if (d && typeof d === "object" && Array.isArray(d.tags) && d.tags.length === 0) delete d.tags;
      }
    }
    return t;
  }).sort(byId("id"));
  out.overrides = values(ws.overrides).sort(byId("id"));
  out.whispers = values(ws.whispers).sort(byId("id"));
  out.deliberations = values(ws.deliberations).sort(byId("id"));
  out.handoffs = values(ws.handoffs).map((h) => {
    if (Array.isArray(h.accepted_task_ids) && h.accepted_task_ids.length === 0) delete h.accepted_task_ids;
    return h;
  }).sort(byId("id"));
  out.snapshots = values(ws.snapshots).sort(byId("id"));
  out.route_decisions = values(ws.route_decisions).sort(byId("id"));
  out.idempotency_keys = { ...ws.idempotency_keys };
  out.audit = ws.audit;
  return dropNull(JSON.parse(JSON.stringify(out)));
}

async function main(): Promise<void> {
  const chunks: Buffer[] = [];
  for await (const c of process.stdin) chunks.push(c as Buffer);
  const req = JSON.parse(Buffer.concat(chunks).toString("utf-8")) as {
    options: { defaultProfiles: string[] };
    workspace: string;
    envelopes: unknown[];
    store?: string;
    lapse_at?: string;
  };
  const options = {
    deterministicIds: true,
    deterministicClock: true,
    enableChain: true,
    defaultProfiles: req.options.defaultProfiles,
  };
  if (!req.store) {
    const coord = new Coordinator(options);
    const responses: unknown[] = [];
    const states: unknown[] = [];
    for (const e of req.envelopes) {
      responses.push(coord.dispatch(e as never));
      states.push(project(coord));
    }
    const chain_head = coord.workspaces.get(req.workspace)?.chain_head ?? null;
    process.stdout.write(JSON.stringify({ responses, states, chain_head }));
    return;
  }
  // The same calls on a SQLite file: every write must have reached it, so a
  // coordinator restarted on the file holds what this one holds.
  const { SqliteStore } = await import("../../packages/coordinator/src/storage/sqlite.ts");
  const errors: string[] = [];
  const store = new SqliteStore(req.store);
  const coord = new Coordinator({ ...options, store, onStoreError: (e) => { errors.push(String(e)); } });
  const responses = req.envelopes.map((e) => coord.dispatch(e as never));
  if (req.lapse_at) coord.checkWhisperLapses?.(req.workspace, req.lapse_at);
  const before = JSON.stringify(sorted(JSON.parse(JSON.stringify(coord.snapshot()))));
  store.close();
  const reopened = new SqliteStore(req.store);
  const after = JSON.stringify(sorted(JSON.parse(JSON.stringify(new Coordinator({ ...options, store: reopened }).snapshot()))));
  reopened.close();
  process.stdout.write(JSON.stringify({ responses, errors, restart_same: before === after }));
}

main().catch((e) => {
  process.stderr.write(String(e?.stack ?? e) + "\n");
  process.exit(1);
});
