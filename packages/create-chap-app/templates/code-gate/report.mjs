// What the gate recorded, as a page to read: `node report.mjs [--json] [--since <ISO date>]`.
//
// Reads every code change from the gate's read API and prints, in
// Markdown: how many were approved as written, approved with an edit,
// sent back for a revision and rejected; how long a decision took; how
// often each model's work was accepted as written; the files reviewers
// edit or send back; and the reviewers' own words, the override
// rationales and the rejection notes, which is where an agent's
// instructions get refined. The figures are the ones the desk's Insights
// view shows, from the same code. `npm run analytics` goes further with
// chap-analytics; this needs nothing beyond the running gate.

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { api, loadGate, TASK_KIND } from "./lib/gate.mjs";
import { minutes, pct, summarise } from "./desk/insights.js";

const here = dirname(fileURLToPath(import.meta.url));

export { summarise };

export function render(s, workspace) {
  const lines = [`# ${workspace}: code changes through the gate`, ""];
  lines.push(`${s.total} change${s.total === 1 ? "" : "s"}: ${s.approved} approved as written, ${s.overridden} approved with an edit, ${s.rejected} rejected, ${s.waiting} waiting. ${s.sent_back} sent back for a revision at least once. Median time to a first decision: ${minutes(s.median_time_ms)}.`, "");
  if (s.models.length) {
    lines.push("## Models", "", "| Model | Changes | Accepted as written | Edited | Sent back | Rejected |", "|---|---|---|---|---|---|");
    for (const m of s.models) lines.push(`| ${m.model} | ${m.changes} | ${m.decided ? pct(m.approve, m.decided) : "n/a"} | ${m.override} | ${m.sent_back} | ${m.reject} |`);
    lines.push("");
  }
  if (s.files.length) {
    lines.push("## Files", "", "| File | Changes | Edited by a reviewer | In changes sent back | In changes rejected |", "|---|---|---|---|---|");
    for (const f of s.files.slice(0, 20)) lines.push(`| \`${f.path}\` | ${f.changes} | ${f.edited} | ${f.sent_back} | ${f.rejected} |`);
    lines.push("");
  }
  const table = (title, pairs) => { if (!pairs.length) return; lines.push(`## ${title}`, "", "| | Count |", "|---|---|"); for (const [k, v] of pairs) lines.push(`| ${k} | ${v} |`); lines.push(""); };
  table("Agents", s.by_agent);
  table("Decisions by reviewer", s.by_reviewer);
  table("Tags", s.tags);
  lines.push("## What reviewers changed", "");
  if (!s.override_rationales.length) lines.push("No override yet.", "");
  for (const o of s.override_rationales) lines.push(`- ${o.task.input?.summary ?? o.task.task_id} (${o.task.task_id}), ${o.reviewer}: ${o.text ?? "no rationale"}${o.tags.length ? ` [${o.tags.join(", ")}]` : ""}`);
  if (s.override_rationales.length) lines.push("");
  lines.push("## What reviewers sent back", "");
  if (!s.rejection_notes.length) lines.push("No rejection yet.", "");
  for (const r of s.rejection_notes) lines.push(`- ${r.task.input?.summary ?? r.task.task_id} (${r.task.task_id}), ${r.reviewer}, ${r.revision ? "revision requested" : "rejected"}: ${r.text ?? "no note"}${r.tags.length ? ` [${r.tags.join(", ")}]` : ""}`);
  if (s.rejection_notes.length) lines.push("");
  lines.push("The rationales and notes above are the material for the agent's instructions: a correction that recurs is a rule to add to AGENT_INSTRUCTIONS.md, or to the prompt in agent.mjs.");
  return lines.join("\n") + "\n";
}

/** Strip the task views out of the summary for JSON output. */
function plain(s) {
  const out = { ...s };
  out.override_rationales = s.override_rationales.map(({ task, ...rest }) => ({ task_id: task.task_id, summary: task.input?.summary ?? null, ...rest }));
  out.rejection_notes = s.rejection_notes.map(({ task, ...rest }) => ({ task_id: task.task_id, summary: task.input?.summary ?? null, ...rest }));
  return out;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  (async () => {
    const argv = process.argv.slice(2);
    const at = argv.indexOf("--since");
    const since = at >= 0 ? argv[at + 1] : null;
    const gate = await loadGate(here);
    const r = await api(gate, `/api/tasks?kind=${TASK_KIND}`);
    const tasks = (r?.tasks ?? []).filter((t) => !since || (t.created_at ?? "") >= since);
    const s = summarise(tasks);
    console.log(argv.includes("--json") ? JSON.stringify(plain(s), null, 2) : render(s, gate.config.workspace));
  })().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
}
