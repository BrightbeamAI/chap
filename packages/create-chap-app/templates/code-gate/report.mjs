// What the gate recorded, as a page to read: `node report.mjs [--json] [--since <ISO date>]`.
//
// Reads every code change from the gate's read API and prints, in
// Markdown: how many were approved as written, approved with an edit,
// sent back for a revision and rejected; how long a decision took; the
// agents and models behind the changes; and the reviewers' own words, the
// override rationales and the rejection notes, grouped, which is where an
// agent's instructions get refined. `npm run analytics` goes further with
// chap-analytics; this needs nothing beyond the running gate.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { api, loadGate, TASK_KIND } from "./lib/gate.mjs";

const here = dirname(fileURLToPath(import.meta.url));

const median = (xs) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const minutes = (ms) => (ms === null ? "n/a" : ms < 60_000 ? `${Math.round(ms / 1000)} s` : `${(ms / 60_000).toFixed(1)} min`);
const count = (xs, key) => { const out = new Map(); for (const x of xs) { const k = key(x) ?? "unknown"; out.set(k, (out.get(k) ?? 0) + 1); } return [...out].sort((a, b) => b[1] - a[1]); };

/** The figures behind the report, from the task views. */
export function summarise(tasks, { since = null } = {}) {
  const rows = tasks.filter((t) => !since || (t.created_at ?? "") >= since);
  const decisions = rows.flatMap((t) => (t.review?.decisions ?? []).map((d) => ({ ...d, task: t })));
  const revisions = decisions.filter((d) => d.kind === "reject" && d.task.history?.some((h) => h.ts > d.ts && h.state === "review_requested"));
  const finalRejects = rows.filter((t) => t.state === "declined");
  const timeToDecision = rows.flatMap((t) => {
    const opened = t.history?.find((h) => h.state === "review_requested")?.ts;
    const decided = t.review?.decisions?.[0]?.ts;
    return opened && decided ? [Date.parse(decided) - Date.parse(opened)] : [];
  });
  return {
    total: rows.length,
    by_state: count(rows, (t) => t.state),
    approved: rows.filter((t) => t.state === "completed" && t.review?.decisions?.at(-1)?.kind === "approve").length,
    overridden: rows.filter((t) => t.state === "completed" && t.review?.decisions?.at(-1)?.kind === "override").length,
    revisions: revisions.length,
    rejected: finalRejects.length,
    open: rows.filter((t) => t.state === "review_requested").length,
    median_time_to_decision_ms: median(timeToDecision),
    by_agent: count(rows, (t) => t.assignee),
    by_model: count(rows, (t) => t.artefact?.drafted_by),
    by_reviewer: count(decisions, (d) => d.reviewer),
    override_rationales: decisions.filter((d) => d.kind === "override").map((d) => ({ task_id: d.task.task_id, summary: d.task.input?.summary, reviewer: d.reviewer, rationale: d.rationale ?? d.comment ?? null, tags: d.tags ?? [] })),
    rejection_notes: decisions.filter((d) => d.kind === "reject").map((d) => ({ task_id: d.task.task_id, summary: d.task.input?.summary, reviewer: d.reviewer, note: d.comment ?? null, revision: revisions.includes(d), tags: d.tags ?? [] })),
    tags: count(decisions.flatMap((d) => (d.tags ?? []).map((tag) => ({ tag }))), (x) => x.tag),
  };
}

export function render(s, workspace) {
  const lines = [`# ${workspace}: code changes through the gate`, ""];
  lines.push(`${s.total} change${s.total === 1 ? "" : "s"}: ${s.approved} approved as written, ${s.overridden} approved with an edit, ${s.rejected} rejected, ${s.open} waiting. ${s.revisions} revision${s.revisions === 1 ? "" : "s"} requested. Median time to a decision: ${minutes(s.median_time_to_decision_ms)}.`, "");
  const table = (title, pairs) => { if (!pairs.length) return; lines.push(`## ${title}`, "", "| | Changes |", "|---|---|"); for (const [k, v] of pairs) lines.push(`| ${k} | ${v} |`); lines.push(""); };
  table("By agent", s.by_agent);
  table("By model", s.by_model);
  table("Decisions by reviewer", s.by_reviewer);
  table("Tags", s.tags);
  lines.push("## What reviewers changed", "");
  if (!s.override_rationales.length) lines.push("No override yet.", "");
  for (const o of s.override_rationales) lines.push(`- ${o.summary ?? o.task_id} (${o.task_id}), ${o.reviewer}: ${o.rationale ?? "no rationale"}${o.tags.length ? ` [${o.tags.join(", ")}]` : ""}`);
  if (s.override_rationales.length) lines.push("");
  lines.push("## What reviewers sent back", "");
  if (!s.rejection_notes.length) lines.push("No rejection yet.", "");
  for (const r of s.rejection_notes) lines.push(`- ${r.summary ?? r.task_id} (${r.task_id}), ${r.reviewer}, ${r.revision ? "revision requested" : "rejected"}: ${r.note ?? "no note"}${r.tags.length ? ` [${r.tags.join(", ")}]` : ""}`);
  if (s.rejection_notes.length) lines.push("");
  lines.push("The rationales and notes above are the material for the agent's instructions: a correction that recurs is a rule to add to AGENT_INSTRUCTIONS.md, or to the prompt in agent.mjs.");
  return lines.join("\n") + "\n";
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  (async () => {
    const argv = process.argv.slice(2);
    const at = argv.indexOf("--since");
    const gate = await loadGate(here);
    const r = await api(gate, `/api/tasks?kind=${TASK_KIND}`);
    const s = summarise(r?.tasks ?? [], { since: at >= 0 ? argv[at + 1] : null });
    console.log(argv.includes("--json") ? JSON.stringify(s, null, 2) : render(s, gate.config.workspace));
  })().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}
