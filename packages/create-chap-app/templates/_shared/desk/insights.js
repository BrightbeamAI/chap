// What the workspace's tasks say, counted in the browser: decisions by
// kind, the rate over time, who decides and how fast, which agents and
// models the work came from, and the reviewers' own words. Nothing here
// needs anything beyond the read API; chap-analytics, when its pages are
// present, is linked for the full report.

import { el } from "./render.js";

const median = (xs) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const count = (xs, key) => { const out = new Map(); for (const x of xs) { const k = key(x) ?? "unknown"; out.set(k, (out.get(k) ?? 0) + 1); } return [...out].sort((a, b) => b[1] - a[1]); };
export const minutes = (ms) => (ms === null ? "n/a" : ms < 60_000 ? `${Math.round(ms / 1000)} s` : ms < 3_600_000 ? `${(ms / 60_000).toFixed(1)} min` : `${(ms / 3_600_000).toFixed(1)} h`);
const pct = (n, d) => (d ? `${Math.round((100 * n) / d)}%` : "n/a");

/** The figures. `tasks` are task views from GET /api/tasks. */
export function summarise(tasks) {
  const decisions = tasks.flatMap((t) => (t.review?.decisions ?? []).map((d) => ({ ...d, task: t })));
  const lastKind = (t) => t.review?.decisions?.at(-1)?.kind;
  const approved = tasks.filter((t) => t.state === "completed" && lastKind(t) === "approve");
  const overridden = tasks.filter((t) => t.state === "completed" && lastKind(t) === "override");
  const rejected = tasks.filter((t) => t.state === "declined");
  const waiting = tasks.filter((t) => t.state === "review_requested");
  const revising = tasks.filter((t) => t.state === "in_progress" && lastKind(t) === "reject");
  const revisions = decisions.filter((d) => d.kind === "reject" && d.task.history?.some((h) => h.ts > d.ts && h.state === "review_requested"));
  const decided = approved.length + overridden.length + rejected.length;
  const times = tasks.flatMap((t) => {
    const opened = t.history?.find((h) => h.state === "review_requested")?.ts;
    const first = t.review?.decisions?.[0]?.ts;
    return opened && first ? [Date.parse(first) - Date.parse(opened)] : [];
  });
  const byDay = new Map();
  for (const d of decisions) {
    const day = d.ts.slice(0, 10);
    const row = byDay.get(day) ?? { day, approve: 0, override: 0, reject: 0 };
    row[d.kind === "abstain" ? "reject" : d.kind] = (row[d.kind === "abstain" ? "reject" : d.kind] ?? 0) + 1;
    byDay.set(day, row);
  }
  return {
    total: tasks.length, decided, approved: approved.length, overridden: overridden.length, rejected: rejected.length,
    waiting: waiting.length, revising: revising.length, revisions: revisions.length,
    acceptance: decided ? (approved.length + overridden.length) / decided : null,
    corrected: decided ? (overridden.length + revisions.length) / decided : null,
    median_time_ms: median(times),
    by_kind: count(tasks, (t) => t.kind),
    by_agent: count(tasks, (t) => t.assignee),
    by_model: count(tasks.filter((t) => t.artefact?.drafted_by), (t) => t.artefact.drafted_by),
    by_reviewer: count(decisions, (d) => d.reviewer),
    by_day: [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day)),
    override_rationales: decisions.filter((d) => d.kind === "override").map((d) => ({ task: d.task, reviewer: d.reviewer, text: d.rationale ?? d.comment ?? null, tags: d.tags ?? [] })),
    rejection_notes: decisions.filter((d) => d.kind === "reject").map((d) => ({ task: d.task, reviewer: d.reviewer, text: d.comment ?? null, tags: d.tags ?? [], revision: revisions.includes(d) })),
    tags: count(decisions.flatMap((d) => (d.tags ?? []).map((tag) => ({ tag }))), (x) => x.tag),
  };
}

function bars(pairs, { max = null } = {}) {
  const top = max ?? Math.max(1, ...pairs.map(([, n]) => n));
  return el("div", { class: "bars" }, pairs.slice(0, 8).map(([name, n]) => el("div", { class: "bar" },
    el("span", { class: "name", title: name, text: name }),
    el("div", { class: "track" }, el("div", { class: "fill", style: `width:${Math.round((100 * n) / top)}%` })),
    el("span", { class: "n", text: String(n) }))));
}

/** Decisions per day as stacked bars, drawn as SVG. */
function dayChart(rows) {
  const W = 600, H = 150, pad = 24;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("class", "chart");
  svg.setAttribute("preserveAspectRatio", "none");
  if (!rows.length) return svg;
  const colours = { approve: "var(--ok)", override: "var(--warn)", reject: "var(--bad)" };
  const max = Math.max(1, ...rows.map((r) => r.approve + r.override + r.reject));
  const bw = Math.max(4, Math.min(40, (W - 2 * pad) / rows.length - 6));
  const step = (W - 2 * pad) / rows.length;
  rows.forEach((r, i) => {
    let y = H - pad;
    for (const kind of ["approve", "override", "reject"]) {
      const h = ((H - 2 * pad) * r[kind]) / max;
      if (!h) continue;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(pad + i * step + (step - bw) / 2));
      rect.setAttribute("y", String(y - h));
      rect.setAttribute("width", String(bw));
      rect.setAttribute("height", String(h));
      rect.setAttribute("fill", colours[kind]);
      rect.setAttribute("rx", "2");
      const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
      title.textContent = `${r.day}: ${r[kind]} ${kind}`;
      rect.append(title);
      svg.append(rect);
      y -= h;
    }
    if (rows.length <= 14 || i % Math.ceil(rows.length / 14) === 0) {
      const t = document.createElementNS("http://www.w3.org/2000/svg", "text");
      t.setAttribute("x", String(pad + i * step + step / 2));
      t.setAttribute("y", String(H - 6));
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("font-size", "10");
      t.setAttribute("fill", "var(--muted)");
      t.textContent = r.day.slice(5);
      svg.append(t);
    }
  });
  return svg;
}

const stat = (label, value, sub) => el("div", { class: "card stat" }, el("div", { class: "label", text: label }), el("div", { class: "value", text: value }), sub ? el("div", { class: "sub", text: sub }) : null);
const section = (title, body, extra) => el("div", { class: "card" }, el("div", { class: "card-head" }, el("h3", { text: title }), extra ? el("span", { class: "spacer" }) : null, extra ?? null), el("div", { class: "card-body" }, body));

/**
 * The Insights view. `analytics` says which chap-analytics pages exist
 * ({ report, refine, cases }), `chain` is { entries, verified, head }.
 */
export function renderInsights(tasks, { chain = null, analytics = null, onOpenTask = null } = {}) {
  const s = summarise(tasks);
  const root = el("div", { class: "stack" });
  root.append(el("div", { class: "cards" },
    stat("Tasks", String(s.total), `${s.waiting} waiting, ${s.revising} being revised`),
    stat("Accepted", pct(s.approved + s.overridden, s.decided), `${s.approved} as written, ${s.overridden} with an edit`),
    stat("Corrected", pct(s.overridden + s.revisions, s.decided), `${s.overridden} edited, ${s.revisions} sent back`),
    stat("Rejected", String(s.rejected), s.decided ? `${pct(s.rejected, s.decided)} of decided` : "nothing decided yet"),
    stat("Time to decision", minutes(s.median_time_ms), "median, first decision"),
    stat("Chain", chain ? String(chain.entries) : "off", chain ? (chain.verified === true ? "verified" : chain.verified === false ? "not verified" : "entries") : "audit-scitt/1.0 is off"),
  ));
  root.append(el("div", { class: "grid-2" },
    section("Decisions by day", el("div", {}, s.by_day.length ? dayChart(s.by_day) : el("div", { class: "muted small", text: "No decision yet." }),
      el("div", { class: "legend" }, el("span", {}, el("i", { style: "background:var(--ok)" }), "approve"), el("span", {}, el("i", { style: "background:var(--warn)" }), "override"), el("span", {}, el("i", { style: "background:var(--bad)" }), "reject")))),
    section("By agent and by model", el("div", { class: "stack" }, bars(s.by_agent), s.by_model.length ? bars(s.by_model) : el("div", { class: "muted small", text: "No artefact names its model yet." }))),
    section("By reviewer", s.by_reviewer.length ? bars(s.by_reviewer) : el("div", { class: "muted small", text: "No decision yet." })),
    section("Tags", s.tags.length ? bars(s.tags) : el("div", { class: "muted small", text: "Tag a decision in the note bar to see them here." })),
  ));
  const words = (list, empty) => (list.length ? el("ul", { class: "words" }, list.slice(0, 12).map((w) => el("li", {},
    el("a", { href: "#", onclick: (e) => { e.preventDefault(); onOpenTask?.(w.task); } }, w.task.input?.summary ?? w.task.task_id), " ",
    el("span", { class: "muted small", text: `${w.reviewer}${w.revision ? ", revision requested" : ""}` }), el("div", { text: w.text ?? "no note" }),
    w.tags.length ? el("div", { class: "small muted", text: w.tags.join(", ") }) : null))) : el("div", { class: "muted small", text: empty }));
  root.append(el("div", { class: "grid-2" },
    section("What reviewers changed", words(s.override_rationales, "No override yet.")),
    section("What reviewers sent back", words(s.rejection_notes, "No rejection yet.")),
  ));
  const links = el("div", { class: "row" });
  if (analytics?.report) links.append(el("a", { class: "btn", href: "/analytics/report.html", target: "_blank", rel: "noopener", text: "Open the interactive report" }));
  if (analytics?.cases) links.append(el("a", { class: "btn", href: "/analytics/cases.jsonl", target: "_blank", rel: "noopener", text: "Evaluation cases (JSONL)" }));
  root.append(section("chap-analytics", el("div", { class: "stack" },
    el("div", { class: "small muted", text: analytics?.report ? "Pages written by chap-analytics from this workspace's store." : "Run the analytics script beside the server (npm run analytics, or python3 analytics.py) with chap-analytics installed, and its report, the evaluation cases and the refinement notes appear here." }),
    links.children.length ? links : null,
    analytics?.refine ? el("div", { class: "md", id: "refine" }) : null,
  )));
  return root;
}

/** A small Markdown rendering: headings, paragraphs, lists, tables, code spans and blocks, bold. */
export function renderMarkdown(text) {
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  const out = [];
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) { const buf = []; i++; while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]); i++; out.push(`<pre class="json">${esc(buf.join("\n"))}</pre>`); continue; }
    const h = line.match(/^(#{1,3}) (.*)$/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (/^\s*[-*] /.test(line)) { const items = []; while (i < lines.length && /^\s*[-*] /.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*[-*] /, ""))}</li>`); out.push(`<ul>${items.join("")}</ul>`); continue; }
    if (/^\s*\d+\. /.test(line)) { const items = []; while (i < lines.length && /^\s*\d+\. /.test(lines[i])) items.push(`<li>${inline(lines[i++].replace(/^\s*\d+\. /, ""))}</li>`); out.push(`<ol>${items.join("")}</ol>`); continue; }
    if (line.startsWith("|")) {
      const rows = []; while (i < lines.length && lines[i].startsWith("|")) rows.push(lines[i++]);
      const cells = (r) => r.replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const [head, , ...body] = rows;
      out.push(`<table><thead><tr>${cells(head).map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${body.map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    if (line.trim() === "") { i++; continue; }
    const buf = []; while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,3} |```|\s*[-*] |\s*\d+\. |\|)/.test(lines[i])) buf.push(lines[i++]);
    out.push(`<p>${inline(buf.join(" "))}</p>`);
  }
  return out.join("\n");
}
