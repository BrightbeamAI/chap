import { Coordinator } from "../../packages/coordinator/src/index.ts";

/** Objects with their keys sorted, so two snapshots compare by content. */
function sorted(x: unknown): unknown {
  if (Array.isArray(x)) return x.map(sorted);
  if (x && typeof x === "object") {
    return Object.fromEntries(Object.keys(x).sort().map(k => [k, sorted((x as Record<string, unknown>)[k])]));
  }
  return x;
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
    const responses = req.envelopes.map((e) => coord.dispatch(e as never));
    const chain_head = coord.workspaces.get(req.workspace)?.chain_head ?? null;
    process.stdout.write(JSON.stringify({ responses, chain_head }));
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
