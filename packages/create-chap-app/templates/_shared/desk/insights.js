// What the workspace's tasks say, counted in the browser: decisions by
// kind, the rate over time, who decides and how fast, which agents and
// models the work came from and how often each was accepted as written,
// for code changes the files reviewers edit or send back, and the
// reviewers' own words. Nothing here needs anything beyond the read API;
// chap-analytics, when its pages are present, is linked for the full
// report. The same summary runs under Node for report.mjs.

import { el } from "./render.js";
import { parsePatch } from "./diff.js";

const median = (xs) => { if (!xs.length) return null; const s = [...xs].sort((a, b) => a - b); const m = Math.floor(s.length / 2); return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const count = (xs, key) => { const out = new Map(); for (const x of xs) { const k = key(x) ?? "unknown"; out.set(k, (out.get(k) ?? 0) + 1); } return [...out].sort((a, b) => b[1] - a[1]); };
export const minutes = (ms) => (ms === null ? "n/a" : ms < 60_000 ? `${Math.round(ms / 1000)} s` : ms < 3_600_000 ? `${(ms / 60_000).toFixed(1)} min` : `${(ms / 3_600_000).toFixed(1)} h`);
export const pct = (n, d) => (d ? `${Math.round((100 * n) / d)}%` : "n/a");

/** Every decision on a task, across review rounds where the server gives them. */
export const decisionsOf = (t) => t.decision_log ?? t.review?.decisions ?? [];

/** How a task ended: approve, override, reject, or null while it is open. */
export function outcomeOf(t) {
  if (t.state === "declined") return "reject";
  if (t.state !== "completed") return null;
  const last = decisionsOf(t).filter((d) => d.kind === "approve" || d.kind === "override").at(-1);
  return last?.kind === "override" ? "override" : "approve";
}

const wasSentBack = (t) => decisionsOf(t).some((d) => d.kind === "reject" && d.request_revision);

/** The model a task's work came from: the one the artefact names, else what drafted it. */
const modelOf = (t) => t.artefact?.model ?? t.artefact?.drafted_by ?? null;

/** The files whose section differs between two patches. */
function filesChanged(before, after) {
  if (typeof before !== "string" || typeof after !== "string") return [];
  const a = Object.fromEntries(parsePatch(before).map((f) => [f.path, f.text]));
  const b = Object.fromEntries(parsePatch(after).map((f) => [f.path, f.text]));
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((p) => a[p] !== b[p]).sort();
}

/** The figures. `tasks` are task views from GET /api/tasks. */
export function summarise(tasks) {
  const decisions = tasks.flatMap((t) => decisionsOf(t).map((d) => ({ ...d, task: t })));
  const outcomes = tasks.map((t) => [t, outcomeOf(t)]);
  const approved = outcomes.filter(([, o]) => o === "approve").map(([t]) => t);
  const overridden = outcomes.filter(([, o]) => o === "override").map(([t]) => t);
  const rejected = outcomes.filter(([, o]) => o === "reject").map(([t]) => t);
  const waiting = tasks.filter((t) => t.state === "review_requested");
  const revising = tasks.filter((t) => t.state === "in_progress" && decisionsOf(t).at(-1)?.kind === "reject");
  const revisions = decisions.filter((d) => d.kind === "reject" && d.request_revision);
  const sentBack = tasks.filter(wasSentBack);
  const decided = approved.length + overridden.length + rejected.length;
  const times = tasks.flatMap((t) => {
    const opened = t.history?.find((h) => h.state === "review_requested")?.ts;
    const first = decisionsOf(t)[0]?.ts;
    return opened && first ? [Date.parse(first) - Date.parse(opened)] : [];
  });
  const byDay = new Map();
  for (const d of decisions) {
    const day = String(d.ts).slice(0, 10);
    const row = byDay.get(day) ?? { day, approve: 0, override: 0, reject: 0 };
    const k = d.kind === "approve" || d.kind === "override" ? d.kind : "reject";
    row[k] += 1;
    byDay.set(day, row);
  }
  // Models: how often each one's work was accepted as written. An artefact
  // names its model, or, in the templates that draft with one, the model is
  // what drafted it.
  const models = new Map();
  for (const t of tasks) {
    const model = modelOf(t);
    if (!model) continue;
    const m = models.get(model) ?? { model, changes: 0, decided: 0, approve: 0, override: 0, reject: 0, sent_back: 0 };
    m.changes++;
    const o = outcomeOf(t);
    if (o) { m.decided++; m[o]++; }
    if (wasSentBack(t)) m.sent_back++;
    models.set(model, m);
  }
  // Code changes and branches: the files reviewers edit, and the files in
  // changes sent back or rejected.
  const files = new Map();
  const fileRow = (path) => files.get(path) ?? files.set(path, { path, changes: 0, edited: 0, sent_back: 0, rejected: 0 }).get(path);
  for (const t of tasks) {
    const a = t.artefact;
    if (!a || (typeof a.patch !== "string" && !Array.isArray(a.commits))) continue;
    const paths = Array.isArray(a.commits) ? [...new Set(a.commits.flatMap((c) => (c.files ?? []).map((f) => f.path)))] : (a.files ?? []).map((f) => f.path);
    for (const path of paths) fileRow(path).changes++;
    if (outcomeOf(t) === "override") for (const path of filesChanged(a.patch, t.output?.patch)) fileRow(path).edited++;
    if (wasSentBack(t)) for (const path of paths) fileRow(path).sent_back++;
    if (outcomeOf(t) === "reject") for (const path of paths) fileRow(path).rejected++;
  }
  return {
    total: tasks.length, decided, approved: approved.length, overridden: overridden.length, rejected: rejected.length,
    waiting: waiting.length, revising: revising.length, revisions: revisions.length, sent_back: sentBack.length,
    acceptance: decided ? (approved.length + overridden.length) / decided : null,
    as_written: decided ? approved.length / decided : null,
    median_time_ms: median(times),
    by_kind: count(tasks, (t) => t.kind),
    by_agent: count(tasks, (t) => t.assignee),
    by_model: count(tasks.filter(modelOf), modelOf),
    models: [...models.values()].sort((x, y) => y.changes - x.changes),
    files: [...files.values()].filter((f) => f.edited || f.sent_back || f.rejected || f.changes).sort((x, y) => (y.edited + y.sent_back + y.rejected) - (x.edited + x.sent_back + x.rejected) || y.changes - x.changes),
    by_reviewer: count(decisions, (d) => d.reviewer),
    by_day: [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day)),
    override_rationales: decisions.filter((d) => d.kind === "override").map((d) => ({ task: d.task, reviewer: d.reviewer, text: d.rationale ?? d.comment ?? null, tags: d.tags ?? [] })),
    rejection_notes: decisions.filter((d) => d.kind === "reject").map((d) => ({ task: d.task, reviewer: d.reviewer, text: d.comment ?? null, tags: d.tags ?? [], revision: !!d.request_revision })),
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
    stat("Accepted as written", pct(s.approved, s.decided), s.decided ? `${s.approved} of ${s.decided} decided` : "nothing decided yet"),
    stat("Approved with an edit", String(s.overridden), s.decided ? `${pct(s.overridden, s.decided)} of decided` : ""),
    stat("Sent back", String(s.sent_back), `${s.revisions} revision request${s.revisions === 1 ? "" : "s"} in all`),
    stat("Rejected", String(s.rejected), s.decided ? `${pct(s.rejected, s.decided)} of decided` : ""),
    stat("Time to decision", minutes(s.median_time_ms), "median, first decision"),
    stat("Chain", chain ? String(chain.entries) : "off", chain ? (chain.verified === true ? "entries, verified" : chain.verified === false ? "entries, verification failed" : "entries") : "the chain is off"),
  ));
  const legend = el("div", { class: "legend" }, el("span", {}, el("i", { style: "background:var(--ok)" }), "approve"), el("span", {}, el("i", { style: "background:var(--warn)" }), "approve with an edit"), el("span", {}, el("i", { style: "background:var(--bad)" }), "reject or send back"));
  const modelTable = s.models.length ? el("table", { class: "table" },
    el("thead", {}, el("tr", {}, ["Model", "Changes", "As written", "Edited", "Sent back", "Rejected"].map((h) => el("th", { text: h })))),
    el("tbody", {}, s.models.map((m) => el("tr", {},
      el("td", { class: "mono", text: m.model }), el("td", { text: String(m.changes) }),
      el("td", { text: m.decided ? pct(m.approve, m.decided) : "n/a" }), el("td", { text: String(m.override) }),
      el("td", { text: String(m.sent_back) }), el("td", { text: String(m.reject) })))))
    : el("div", { class: "muted small", text: "No artefact names the model that drafted it yet." });
  root.append(el("div", { class: "grid-2" },
    section("Decisions by day", el("div", {}, s.by_day.length ? dayChart(s.by_day) : el("div", { class: "muted small", text: "No decision yet." }), legend)),
    section("Agents and reviewers", el("div", { class: "stack" },
      s.by_agent.length ? bars(s.by_agent) : el("div", { class: "muted small", text: "No task yet." }),
      s.by_reviewer.length ? bars(s.by_reviewer) : el("div", { class: "muted small", text: "No decision yet." }))),
  ));
  root.append(section("Models", el("div", { class: "stack" }, modelTable, el("div", { class: "small muted", text: "A model whose work is accepted as written more often needs less of the reviewers' time. Compare them on the same kind of task." }))));
  if (s.files.length) {
    const top = s.files.slice(0, 12);
    root.append(section("Files", el("div", { class: "stack" },
      el("table", { class: "table" },
        el("thead", {}, el("tr", {}, ["File", "Changes", "Edited by a reviewer", "In changes sent back", "In changes rejected"].map((h) => el("th", { text: h })))),
        el("tbody", {}, top.map((f) => el("tr", {}, el("td", { class: "mono", text: f.path }), el("td", { text: String(f.changes) }), el("td", { class: f.edited ? "warn-text" : "", text: String(f.edited) }), el("td", { text: String(f.sent_back) }), el("td", { class: f.rejected ? "bad-text" : "", text: String(f.rejected) }))))),
      el("div", { class: "small muted", text: "Where reviewers step in. A file that keeps being edited or sent back is where the agent's instructions need a rule, or where the code needs a clearer shape." }))));
  }
  root.append(el("div", { class: "grid-2" },
    section("Tags", s.tags.length ? bars(s.tags) : el("div", { class: "muted small", text: "Tag a decision in the note bar to see them here." })),
    section("Task kinds", bars(s.by_kind)),
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
  const sum = analytics?.summary;
  root.append(section("chap-analytics", el("div", { class: "stack" },
    el("div", { class: "small muted", text: sum
      ? `Written ${new Date(sum.written).toLocaleString()} from the store: ${sum.tasks} tasks, ${sum.decisions} decisions, ${sum.overrides} overrides, ${sum.cases} evaluation cases. The analytics script writes them again when it runs; with --watch it keeps them current.`
      : "Run the analytics script beside the server (npm run analytics, or python3 analytics.py) with chap-analytics installed, and its interactive report, the evaluation cases and the refinement page appear here." }),
    links.children.length ? links : null,
    analytics?.refine ? el("div", { class: "md", id: "refine" }) : null,
  )));
  return root;
}

/** A small Markdown rendering: headings, paragraphs, lists, tables, code spans and blocks, bold. */
export function renderMarkdown(text) {
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  // A link is kept only for an http(s) address or a path on this server;
  // anything else, a javascript: URL included, stays as its text.
  const safeUrl = (u) => /^(https?:\/\/|\/(?!\/)|\.\/|#)/i.test(u) && !/["'<>\s]/.test(u);
  const inline = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => (safeUrl(url) ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : `${text} (${url})`));
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
