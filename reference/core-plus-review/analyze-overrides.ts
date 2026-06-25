/**
 * Override analyser - reads the workspace audit log, aggregates
 * `decide.override` envelopes, and produces the learning-data report.
 *
 * This is what makes "your overrides become training data for free"
 * concrete. Run after the demo client (or your real workspace).
 *
 * Usage:
 *   # HTTP mode (default; coordinator runs as a server)
 *   tsx analyze-overrides.ts                              # default workspace
 *   tsx analyze-overrides.ts wsp_my_workspace             # named workspace
 *   CHAP_URL=http://prod.example.org/chap tsx analyze-overrides.ts wsp_prod
 *
 *   # SQLite mode (no server needed; reads the SqliteStore directly)
 *   tsx analyze-overrides.ts --db ./chap.db wsp_my_workspace
 */

// ---- arg parsing ---------------------------------------------------

const argv = process.argv.slice(2);
let DB_PATH: string | undefined;
const positional: string[] = [];
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--db" && argv[i + 1]) {
    DB_PATH = argv[++i];
  } else if (argv[i].startsWith("--db=")) {
    DB_PATH = argv[i].slice(5);
  } else {
    positional.push(argv[i]);
  }
}

const CHAP = process.env.CHAP_URL ?? "http://localhost:8080/chap";
const WS   = positional[0] ?? "wsp_support_triage";

interface OverrideParams {
  from:         string;
  task_id:      string;
  ts:           string;
  tags?:        string[];
  rationale?:   string;
  policy_refs?: string[];
  diff?:        { op: string; path: string }[];
  based_on_artefact?: any;
  // CHAP 0.2.1 - optional artefact-identity / intent fields.
  // Present when the client emitted them; otherwise undefined.
  logical_id?:       string;
  instance_id?:      string;
  intent_preserved?: boolean;
}

// ---- transport: HTTP or SqliteStore --------------------------------

async function call(method: string, params: Record<string, unknown>): Promise<any> {
  const res = await fetch(CHAP, {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({ jsonrpc: "2.0", id: "a1", method, params }),
  });
  const body = await res.json() as { result?: any; error?: any };
  if (body.error) throw new Error(`${body.error.code}: ${body.error.message}`);
  return body.result;
}

async function loadAuditFromSqlite(dbPath: string, workspace: string): Promise<any> {
  // Lazy-import only when the --db flag is used so the HTTP path
  // doesn't pull better-sqlite3 into the dependency tree.
  const { SqliteStore } = await import("@chap/coordinator/storage/sqlite");
  const store = new SqliteStore(dbPath);
  const records = store.load();
  const r = records.find(r => r.id === workspace);
  store.close?.();
  if (!r) throw new Error(`Workspace ${workspace} not found in ${dbPath}`);
  const data = r.data as { audit?: any[] };
  return { entries: data.audit ?? [] };
}

function bar(value: number, max: number, width = 24): string {
  const filled = Math.round((value / Math.max(max, 1)) * width);
  return "█".repeat(filled) + "·".repeat(width - filled);
}

function pct(value: number, total: number): string {
  return ((value / Math.max(total, 1)) * 100).toFixed(1) + "%";
}

async function main(): Promise<void> {
  // Fetch all overrides
  const audit = DB_PATH
    ? await loadAuditFromSqlite(DB_PATH, WS)
    : await call("audit.read", {
        workspace: WS,
        filter: { method: "decide.override" },
      });

  const allEntries = audit.entries ?? [];
  const overrideEnvelopes = DB_PATH
    ? allEntries.filter((e: any) => e.envelope?.method === "decide.override")
    : allEntries; // HTTP path already filtered server-side
  const entries: OverrideParams[] = overrideEnvelopes.map((e: any) => e.envelope.params);
  const total = entries.length;

  console.log("\n" + "═".repeat(60));
  console.log(`  Override Learning-Data Report - ${WS}`);
  console.log("═".repeat(60));
  console.log(`\nTotal overrides:  ${total}`);

  if (total === 0) {
    console.log("\nNo overrides yet. Run the demo client first.");
    return;
  }

  // -------- Tag distribution --------
  const tagCounts = new Map<string, number>();
  for (const e of entries) {
    for (const t of e.tags ?? []) {
      tagCounts.set(t, (tagCounts.get(t) ?? 0) + 1);
    }
  }
  const tagsSorted = [...tagCounts.entries()].sort((a, b) => b[1] - a[1]);
  const tagMax = tagsSorted[0]?.[1] ?? 0;

  console.log(`\n┌─ Tag frequency ${"─".repeat(43)}┐`);
  for (const [tag, count] of tagsSorted) {
    console.log(`│  ${tag.padEnd(25)} ${bar(count, tagMax, 18)} ${String(count).padStart(3)} (${pct(count, total)})`);
  }
  console.log(`└${"─".repeat(58)}┘`);

  // -------- By reviewer --------
  const byReviewer = new Map<string, number>();
  for (const e of entries) {
    byReviewer.set(e.from, (byReviewer.get(e.from) ?? 0) + 1);
  }
  const reviewersSorted = [...byReviewer.entries()].sort((a, b) => b[1] - a[1]);
  const revMax = reviewersSorted[0]?.[1] ?? 0;

  console.log(`\n┌─ Overrides by reviewer ${"─".repeat(35)}┐`);
  for (const [r, count] of reviewersSorted) {
    console.log(`│  ${r.padEnd(40).slice(0, 40)} ${bar(count, revMax, 10)} ${count}`);
  }
  console.log(`└${"─".repeat(58)}┘`);

  // -------- Policy refs --------
  const policyCounts = new Map<string, number>();
  for (const e of entries) {
    for (const p of e.policy_refs ?? []) {
      policyCounts.set(p, (policyCounts.get(p) ?? 0) + 1);
    }
  }
  const policiesSorted = [...policyCounts.entries()].sort((a, b) => b[1] - a[1]);

  if (policiesSorted.length > 0) {
    console.log(`\n┌─ Policies cited ${"─".repeat(42)}┐`);
    const polMax = policiesSorted[0][1];
    for (const [p, count] of policiesSorted) {
      console.log(`│  ${p.padEnd(35)} ${bar(count, polMax, 12)} ${count}`);
    }
    console.log(`└${"─".repeat(58)}┘`);
  }

  // -------- Paths edited --------
  const pathCounts = new Map<string, number>();
  for (const e of entries) {
    for (const op of e.diff ?? []) {
      pathCounts.set(op.path, (pathCounts.get(op.path) ?? 0) + 1);
    }
  }
  const pathsSorted = [...pathCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);

  if (pathsSorted.length > 0) {
    console.log(`\n┌─ Most-edited fields (top 10) ${"─".repeat(28)}┐`);
    const pathMax = pathsSorted[0][1];
    for (const [path, count] of pathsSorted) {
      console.log(`│  ${path.padEnd(35)} ${bar(count, pathMax, 12)} ${count}`);
    }
    console.log(`└${"─".repeat(58)}┘`);
  }

  // -------- Intent-preserved breakdown (CHAP 0.2.1) --------
  // When clients emit intent_preserved, we can separate "human
  // refined the agent's expression of the same decision" from
  // "human substituted a different decision". This is the
  // distinction the protocol surfaces; see SPEC §9.4.
  const intentTagged   = entries.filter(e => e.intent_preserved !== undefined);
  if (intentTagged.length > 0) {
    const refined   = intentTagged.filter(e => e.intent_preserved === true ).length;
    const replaced  = intentTagged.filter(e => e.intent_preserved === false).length;
    const untagged  = total - intentTagged.length;

    console.log(`\n┌─ Override intent ${"─".repeat(41)}┐`);
    console.log(`│  refined  (same decision, better delivery)  ${bar(refined,  intentTagged.length, 14)} ${refined}  (${pct(refined,  total)})`);
    console.log(`│  replaced (different decision substituted)  ${bar(replaced, intentTagged.length, 14)} ${replaced}  (${pct(replaced, total)})`);
    if (untagged > 0) {
      console.log(`│  untagged (client did not emit field)       ${" ".repeat(16)} ${untagged}  (${pct(untagged, total)})`);
    }
    console.log(`└${"─".repeat(58)}┘`);
  }

  // -------- The interpretation --------
  console.log("\n┌─ What this tells you " + "─".repeat(37) + "┐");
  if (tagsSorted.length > 0) {
    const [topTag, topCount] = tagsSorted[0];
    console.log(`│`);
    console.log(`│  Dominant pattern: ${topTag} (${pct(topCount, total)} of overrides).`);
    if (topTag.includes("tone")) {
      console.log(`│  → Your drafts are getting the tone wrong consistently.`);
      console.log(`│  → Action: revise the agent's tone prompts or rubric.`);
    } else if (topTag.includes("severity")) {
      console.log(`│  → Your severity classification is biased.`);
      console.log(`│  → Action: re-anchor the severity rubric with examples.`);
    } else if (topTag.includes("length")) {
      console.log(`│  → Drafts run too long or too short for the use case.`);
      console.log(`│  → Action: add explicit length guidance to prompts.`);
    } else if (topTag.includes("factual")) {
      console.log(`│  → Drafts contain factual errors.`);
      console.log(`│  → Action: improve the agent's retrieval / grounding.`);
    } else {
      console.log(`│  → Investigate this pattern - it's the cheapest win.`);
    }
  }
  console.log(`│`);
  console.log("└" + "─".repeat(58) + "┘");

  console.log("\nThis report was produced from the CHAP audit log alone.");
  console.log("No additional instrumentation. No retroactive tagging.");
  console.log("Every override fed into this report was captured as a side effect");
  console.log("of normal work. That's the dividend.\n");
}

main().catch((e) => {
  console.error("Analysis failed:", e);
  console.error("Is the server running and the workspace populated?");
  process.exit(1);
});
