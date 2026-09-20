// The report's page logic: filters, headlines, charts, drill-through, lineage.
//
// Everything on the page is computed from the tables embedded in the file.
// The chart specifications come from chap_analytics.charts with their initial
// data; on a filter change the data is recomputed here with CHAP_STATS and
// the chart is embedded again with the new values.
(function () {
  "use strict";
  const S = window.CHAP_STATS;
  const payload = JSON.parse(document.getElementById("chap-data").textContent);
  const D = payload.data, T = payload.charts, Q = payload.questions, BRIEFS = payload.briefs;
  const META = D.meta;
  const HOUR = 3600;
  const state = { from: "", to: "", kind: "", agent: "", reviewer: "", mode: "", tag: "" };
  const briefByName = Object.fromEntries(BRIEFS.map((b) => [b.name, b]));
  const hLookup = D.cusum_h || [];

  const SECTIONS = [
    { id: "overrides", title: "How often the agent's work is changed", charts: ["rate_over_time", "rate_by_kind", "rate_by_reviewer", "refine_reverse"], brief: "overrides" },
    { id: "paths", title: "Where the corrections land", charts: ["patch_heatmap"], brief: "paths", drill: true },
    { id: "calibration", title: "Whether confidence can be trusted", charts: ["reliability"], brief: "calibration" },
    { id: "promotion", title: "Whether an agent is ready to promote", charts: ["promotion", "sequential"], brief: "promotion" },
    { id: "latency", title: "How long decisions take", charts: ["survival", "latency_by_reviewer"], brief: "latency", queue: true },
    { id: "agreement", title: "Whether reviewers agree", charts: ["agreement"], brief: "agreement" },
    { id: "whispers", title: "Whether agents get answers", charts: ["whispers"], brief: "whispers" },
    { id: "handoffs", title: "Whether handoffs are accepted", charts: ["handoffs"], brief: "handoffs" },
    { id: "assurance", title: "Whether the record holds up", charts: ["assurance"], brief: "assurance" },
    { id: "drift", title: "Whether the correction rate has moved", charts: ["cusum"], brief: "drift" },
    { id: "collaboration", title: "Who carries the reviewing, and whether a person was on the path", charts: ["collaboration"], brief: "concentration", extra: ["coverage", "duties"] },
  ];

  // ------------------------------------------------------------ utilities

  const pct = (x) => (x === null || x === undefined || Number.isNaN(x) ? "n/a" : (x * 100).toFixed(0) + "%");
  const num = (x, d) => (x === null || x === undefined || Number.isNaN(x) ? "n/a" : Number(x).toFixed(d === undefined ? 2 : d));
  const hours = (s) => (s === null || s === undefined || Number.isNaN(s) ? "n/a" : s < HOUR ? (s / 60).toFixed(0) + " min" : s < 86400 * 2 ? (s / HOUR).toFixed(1) + " h" : (s / 86400).toFixed(1) + " d");
  const el = (tag, attrs, children) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else if (k === "html") e.innerHTML = v; else e.setAttribute(k, v);
    }
    for (const c of children || []) e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    return e;
  };
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const kindOf = (uri) => (typeof uri === "string" && uri.includes(":") ? uri.split(":")[0] : null);

  function filterActive() {
    return Object.values(state).some((v) => v);
  }

  // ------------------------------------------------------------ filtering

  function inRange(iso) {
    if (!iso) return true;
    if (state.from && iso < state.from) return false;
    if (state.to && iso > state.to + "T23:59:59.999Z") return false;
    return true;
  }

  function filtered() {
    const tasks = D.tasks.filter((t) =>
      (!state.kind || t.kind === state.kind) &&
      (!state.agent || t.assignee === state.agent) &&
      (!state.mode || t.mode === state.mode) &&
      (!state.reviewer || t.reviewer === state.reviewer) &&
      (!state.tag || (t.tags || []).includes(state.tag)) &&
      inRange(t.settled_at || t.created_at));
    const ids = new Set(tasks.map((t) => t.task_id));
    const decisions = D.decisions.filter((d) => ids.has(d.task_id) && (!state.reviewer || d.reviewer === state.reviewer));
    const overrides = D.overrides.filter((o) => ids.has(o.task_id) && (!state.reviewer || o.reviewer === state.reviewer));
    const opKeys = new Set(overrides.map((o) => o.task_id + "" + o.seq));
    const patch_ops = D.patch_ops.filter((o) => opKeys.has(o.task_id + "" + o.seq));
    const passes = D.passes.filter((p) => ids.has(p.task_id) && (!state.reviewer || !p.reviewer || p.reviewer === state.reviewer));
    const whispers = D.whispers.filter((w) => (!w.task_id || ids.has(w.task_id)) && inRange(w.asked_at) &&
      (!state.agent || w.asker === state.agent));
    const handoffs = D.handoffs.filter((h) => inRange(h.proposed_at) &&
      (!state.agent || h.proposer === state.agent || h.recipient === state.agent) &&
      (!state.reviewer || h.proposer === state.reviewer || h.recipient === state.reviewer));
    const events = D.events.filter((e) => inRange(e.ts) && (!e.task_id || !(state.kind || state.agent || state.mode || state.tag || state.reviewer) || ids.has(e.task_id)));
    return { tasks, decisions, overrides, patch_ops, passes, whispers, handoffs, events };
  }

  // ------------------------------------------------------------ chart data builders

  function rename(rows, from, to) {
    return rows.map((r) => { const o = Object.assign({}, r); for (const [a, b] of Object.entries(from)) { o[b] = r[a]; } return o; });
  }

  const builders = {
    rate_over_time(F) {
      return S.ratesOverTime(F.tasks, META.freq).map((r) => ({ period: r.period, n: r.n, rate: r.override_rate, low: r.override_low, high: r.override_high, sufficient: r.sufficient }));
    },
    rate_by_kind(F) {
      return S.rates(F.tasks, "kind").map((r) => ({ kind: r.kind, n: r.n, rate: r.override_rate, low: r.override_low, high: r.override_high, sufficient: r.sufficient }));
    },
    rate_by_reviewer(F) {
      return S.rates(F.tasks, "reviewer").filter((r) => r.reviewer).map((r) => ({ reviewer: r.reviewer, n: r.n, rate: r.override_rate, low: r.override_low, high: r.override_high, sufficient: r.sufficient }));
    },
    refine_reverse(F) {
      const rows = S.refineReverse(F.overrides, "task_kind");
      const out = [];
      for (const r of rows) for (const c of ["refining", "reversing", "unstated"]) out.push({ task_kind: r.task_kind, category: c, count: r[c] });
      return out;
    },
    patch_heatmap(F) {
      return S.patchPaths(F.patch_ops, "task_kind").map((r) => ({ task_kind: r.task_kind, top_path: r.top_path, n: r.n, overrides: r.overrides, share: r.share }));
    },
    reliability(F, spec) {
      const c = S.calibration(F.tasks, 10);
      if (spec) spec.title.subtitle = c.n ? `${c.n} decided tasks with a confidence; expected calibration error ${num(c.ece, 3)}; bins with fewer than five tasks are left out` : "";
      return c.table.filter((b) => b.n >= 5).map((b) => ({ bin: b.bin, mean_confidence: b.mean_confidence, acceptance: b.acceptance, low: b.low, high: b.high, n: b.n }));
    },
    survival(F) {
      return S.survival(F.passes).map((r) => ({ time: r.time_s / HOUR, survival: r.survival, low: r.low, high: r.high, at_risk: r.at_risk, events: r.events, censored: r.censored }));
    },
    latency_by_reviewer(F) {
      return S.latencyBy(F.passes, "reviewer").filter((r) => r.reviewer).map((r) => ({ reviewer: r.reviewer, n: r.n, open: r.open, median: r.median_s / HOUR, p90: r.p90_s / HOUR, sufficient: r.sufficient }));
    },
    promotion(F) {
      const rows = S.promotion(F.tasks, META.threshold, "assignee");
      const out = [];
      for (const r of rows) {
        const a = r.alpha, b = r.beta;
        const lb = S.lgamma(a + b) - S.lgamma(a) - S.lgamma(b);
        for (let i = 0; i < 200; i++) {
          const x = 0.001 + (0.998 * i) / 199;
          const dens = Math.exp(lb + (a - 1) * Math.log(x) + (b - 1) * Math.log(1 - x));
          out.push({ group: r.assignee, rate: x, density: dens, p_below: r.p_below_threshold, n: r.n });
        }
      }
      return out;
    },
    sequential(F, spec) {
      // The test stops at its first crossing per agent; rows after it are
      // flagged so the line ends there, and the stop is marked and labelled.
      const rows = S.sequential(F.tasks, META.threshold, Math.min(0.99, META.threshold + 0.15), "assignee");
      const stoppedAt = {}, stops = [], out = [];
      for (const r of rows) {
        const g = r.assignee;
        const after = stoppedAt[g] !== undefined;
        if (!after && r.verdict !== "continue") {
          stoppedAt[g] = r.i;
          stops.push({ assignee: g, i: r.i, llr: r.llr, text: `${r.verdict} at task ${r.i}` });
        }
        out.push({ i: r.i, llr: r.llr, assignee: g, verdict: r.verdict, settled_at: r.settled_at, after });
      }
      if (spec && spec.layer) for (const l of spec.layer) if (l.data && l.data.values && l.mark && (l.mark.type === "point" || l.mark.type === "text")) l.data.values = stops;
      return out;
    },
    agreement(F) {
      const pairs = S.pairwiseAgreement(F.decisions);
      const out = [];
      for (const p of pairs) {
        out.push({ reviewer_a: p.reviewer_a, reviewer_b: p.reviewer_b, n_passes: p.n_passes, kappa: p.kappa, agreement_observed: p.agreement_observed, sufficient: p.sufficient });
        out.push({ reviewer_a: p.reviewer_b, reviewer_b: p.reviewer_a, n_passes: p.n_passes, kappa: p.kappa, agreement_observed: p.agreement_observed, sufficient: p.sufficient });
      }
      return out;
    },
    whispers(F) {
      return S.whispers(F.whispers, "asker").map((r) => ({ asker: r.asker, n: r.n, answered: r.answered, lapsed: r.lapsed, pending: r.pending, lapse_rate: r.lapse_rate, low: r.low, high: r.high, sufficient: r.sufficient }));
    },
    handoffs(F) {
      return S.handoffs(F.handoffs, "recipient").map((r) => ({ recipient: r.recipient, n: r.n, accepted: r.accepted, declined: r.declined, open: r.open, accept_rate: r.accept_rate, low: r.low, high: r.high, median_response_s: r.median_response_s }));
    },
    assurance(F) {
      const out = [];
      for (const r of S.assurance(F.events, "D")) {
        out.push({ period: r.period, n: r.n, share: r.chained_share, property: "hash-linked" });
        out.push({ period: r.period, n: r.n, share: r.signed_share, property: "signed" });
        out.push({ period: r.period, n: r.n, share: r.scitt_share, property: "SCITT submitted" });
      }
      return out;
    },
    cusum(F, spec) {
      const rows = S.cusum(F.tasks, { shift: META.shift, threshold: thresholdFor });
      if (spec && rows.length) spec.title.subtitle = `Tuned to a move from ${pct(rows[0].target)} to ${pct(rows[0].detect)}; the dashed line is the decision interval`;
      return rows.map((r) => ({ i: r.i, statistic: r.statistic, threshold: r.threshold, alarm: r.alarm, rate_so_far: r.rate_so_far, group: "all", settled_at: r.settled_at, task_id: r.task_id }));
    },
    collaboration(F, spec) {
      const edges = collaborationEdges(F);
      const pos = Object.fromEntries(D.positions.map((p) => [p.node, p]));
      const rows = edges.filter((e) => pos[e.source] && pos[e.target]).map((e) => Object.assign({}, e, {
        x: pos[e.source].x, y: pos[e.source].y, x2: pos[e.target].x, y2: pos[e.target].y }));
      const present = new Set(rows.flatMap((e) => [e.source, e.target]));
      const inW = {}, outW = {};
      for (const e of rows) { inW[e.target] = (inW[e.target] || 0) + e.weight; outW[e.source] = (outW[e.source] || 0) + e.weight; }
      const cen = Object.fromEntries(D.centrality.map((c) => [c.participant, c]));
      const nodes = D.positions.filter((p) => present.has(p.node)).map((p) => ({ node: p.node, kind: kindOf(p.node), x: p.x, y: p.y,
        size: (inW[p.node] || 0) + (outW[p.node] || 0), in_weight: inW[p.node] || 0, out_weight: outW[p.node] || 0,
        betweenness: cen[p.node] ? cen[p.node].betweenness : null }));
      // The name sits above its circle by the circle's radius plus a margin,
      // in the y axis's own units, under the frame charts.py draws with:
      // circle areas 200 to 2200 square pixels, a 440 pixel plot, axes
      // spanning -0.08 to 1.08.
      const sizeMax = nodes.reduce((m, n) => Math.max(m, n.size), 0) || 1;
      const perPixel = 1.16 / 440;
      const labelY = (n) => n.y + (Math.sqrt((200 + 2000 * (n.size / sizeMax)) / Math.PI) + 10) * perPixel;
      if (spec && spec.layer) {
        spec.layer[1].data.values = nodes;
        spec.layer[1].encoding.size.scale.domain = [0, sizeMax];
        spec.layer[2].data.values = nodes.map((n) => ({ node: n.node, x: n.x, label_y: labelY(n) }));
      }
      return rows;
    },
  };

  function thresholdFor(p0) {
    if (!hLookup.length) return 4;
    let best = hLookup[0];
    for (const row of hLookup) if (Math.abs(row.p0 - p0) < Math.abs(best.p0 - p0)) best = row;
    return best.h;
  }

  function collaborationEdges(F) {
    const out = [];
    const add = (source, target, relation, rows, latencyKey) => {
      const lat = rows.map((r) => r[latencyKey]).filter((x) => x !== null && x !== undefined);
      out.push({ source, target, relation, weight: rows.length, mean_latency_s: lat.length ? S.mean(lat) : null });
    };
    const byPair = (rows, a, b) => {
      const g = new Map();
      for (const r of rows) { if (!r[a] || !r[b]) continue; const k = r[a] + "" + r[b]; if (!g.has(k)) g.set(k, []); g.get(k).push(r); }
      return g;
    };
    for (const [k, rows] of byPair(F.decisions, "reviewer", "assignee")) { const [a, b] = k.split(""); add(a, b, "reviewed", rows, "latency_s"); }
    for (const [k, rows] of byPair(F.tasks, "delegator", "assignee")) { const [a, b] = k.split(""); if (a !== b) add(a, b, "delegated", rows, "none"); }
    for (const [k, rows] of byPair(F.handoffs, "proposer", "recipient")) { const [a, b] = k.split(""); add(a, b, "handed_off", rows, "response_s"); }
    const answered = F.whispers.filter((w) => w.answered_by);
    for (const [k, rows] of byPair(answered, "asker", "answered_by")) { const [a, b] = k.split(""); add(a, b, "whispered", rows, "response_s"); }
    return out;
  }

  // ------------------------------------------------------------ headlines

  function headlineCards(F) {
    const cards = [];
    const r = S.rates(F.tasks)[0];
    const rr = S.refineReverse(F.overrides)[0];
    cards.push({ id: "overrides", label: "Work changed by reviewers", value: r ? pct(r.override_rate + r.reject_rate) : "n/a",
      detail: r ? `${r.n} decided; corrected ${pct(r.override_low)} to ${pct(r.override_high)}` + (rr && (rr.refining + rr.reversing) ? `; ${pct(rr.refine_share)} refining` : "") : "no decided tasks",
      thin: !(r && r.sufficient), tone: r && r.override_rate + r.reject_rate > 0.4 ? "warn" : "" });
    const paths = S.patchPaths(F.patch_ops, "task_kind");
    const top = paths.slice().sort((a, b) => b.n - a.n)[0];
    cards.push({ id: "paths", label: "Most corrected part", value: top ? top.top_path : "n/a",
      detail: top ? `${top.n} of ${top.overrides} ${top.task_kind} corrections (${pct(top.share)})` : "no overrides", thin: !top });
    const c = S.calibration(F.tasks, 10);
    cards.push({ id: "calibration", label: "Calibration error", value: c.n ? num(c.ece, 2) : "n/a",
      detail: c.n ? `${c.n} tasks with a confidence; Brier ${num(c.brier, 2)}` : "no confidence reported", thin: !c.sufficient, tone: c.n && c.ece > 0.1 ? "warn" : c.n ? "ok" : "" });
    const prom = S.promotion(F.tasks, META.threshold, "assignee");
    const p0 = prom.slice().sort((a, b) => b.n - a.n)[0];
    cards.push({ id: "promotion", label: `Chance the substantive correction rate is under ${pct(META.threshold)}`, value: p0 ? pct(p0.p_below_threshold) : "n/a",
      detail: p0 ? `${p0.assignee}: ${p0.against} substantive in ${p0.n}, ${pct(p0.low)} to ${pct(p0.high)}` : "no decided tasks", thin: !(p0 && p0.sufficient), tone: p0 && p0.p_below_threshold >= 0.9 ? "ok" : p0 ? "warn" : "" });
    const decided = F.passes.filter((p) => p.event).map((p) => p.duration_s);
    const open = F.passes.filter((p) => !p.event);
    cards.push({ id: "latency", label: "Median time to decision", value: decided.length ? hours(S.median(decided)) : "n/a",
      detail: `${decided.length} decided passes; ${open.length} waiting; 90th percentile ${hours(S.quantile(decided, 0.9))}`, thin: decided.length < 10, tone: open.length > Math.max(3, 0.1 * F.passes.length) ? "warn" : "" });
    const ag = S.agreement(F.decisions)[0];
    cards.push({ id: "agreement", label: "Reviewer agreement (kappa)", value: ag ? num(ag.kappa, 2) : "n/a",
      detail: ag ? `${ag.n_passes} passes decided by ${ag.raters}; agreed ${pct(ag.agreement_observed)}` : "no pass had two reviewers", thin: !(ag && ag.sufficient) });
    const w = S.whispers(F.whispers)[0];
    cards.push({ id: "whispers", label: "Whispers that lapsed", value: w ? pct(w.lapse_rate) : "n/a",
      detail: w ? `${w.n} asked; ${w.lapsed} lapsed; median answer ${hours(w.median_response_s)}` : "no whispers", thin: !(w && w.sufficient), tone: w && w.lapse_rate > 0.25 ? "warn" : "" });
    const h = S.handoffs(F.handoffs)[0];
    cards.push({ id: "handoffs", label: "Handoffs accepted", value: h ? pct(h.accept_rate) : "n/a",
      detail: h ? `${h.n} proposed; ${h.declined} declined; ${h.open} open` : "no handoffs", thin: !(h && h.sufficient) });
    const n = F.events.length;
    const chained = F.events.filter((e) => e.chained).length, signed = F.events.filter((e) => e.signed).length, sub = F.events.filter((e) => e.scitt_submitted).length;
    cards.push({ id: "assurance", label: "Entries hash-linked", value: n ? pct(chained / n) : "n/a",
      detail: n ? `${n} entries; ${pct(signed / n)} signed; ${pct(sub / n)} SCITT submitted` : "no entries", thin: !n, tone: n && chained === n ? "ok" : "warn" });
    const cs = S.cusum(F.tasks, { shift: META.shift, threshold: thresholdFor });
    const alarms = cs.filter((x) => x.alarm).length;
    cards.push({ id: "drift", label: "Drift alarms", value: cs.length ? String(alarms) : "n/a",
      detail: cs.length ? `${cs.length} decided tasks watched; baseline ${pct(cs[0].target)}` : "no decided tasks", thin: cs.length < 50, tone: alarms ? "bad" : cs.length ? "ok" : "" });
    const conc = S.concentration(F.decisions)[0];
    cards.push({ id: "collaboration", brief: "concentration", label: "Top reviewer's share of one agent's decisions", value: conc ? pct(conc.top_share) : "n/a",
      detail: conc ? `${conc.top_reviewer} on ${conc.assignee}; ${conc.n_reviewers} reviewers over ${conc.n_decisions} decisions` : "no decisions", thin: !(conc && conc.sufficient), tone: conc && conc.top_share > 0.6 ? "warn" : "" });
    const cov = S.coverage(F.tasks, F.decisions);
    cards.push({ id: "collaboration", brief: "coverage", label: "Outcomes with a person on the path", value: cov.shipped ? pct(cov.share) : "n/a",
      detail: cov.shipped ? `${cov.covered} of ${cov.shipped} shipped; ${cov.uncovered.length} without` : "nothing shipped yet", thin: !cov.shipped, tone: cov.shipped && cov.share < 1 ? "bad" : cov.shipped ? "ok" : "" });
    const du = S.duties(F.tasks, F.decisions, F.handoffs);
    cards.push({ id: "collaboration", brief: "duties", label: "Separation-of-duties findings", value: String(du.length),
      detail: du.length ? Object.entries(du.reduce((m, d) => (m[d.check] = (m[d.check] || 0) + 1, m), {})).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ") : "none", tone: du.length ? "bad" : "ok" });
    return cards;
  }

  function renderHeadlines(F) {
    const host = document.getElementById("headlines");
    host.innerHTML = "";
    for (const c of headlineCards(F)) {
      const brief = briefByName[c.brief || c.id];
      const card = el("div", { class: "card" + (c.thin ? " thin" : "") }, [
        el("div", { class: "label", text: c.label }),
        el("div", { class: "value " + (c.tone || ""), text: c.value }),
        el("div", { class: "detail", text: c.detail }),
      ]);
      if (brief && !filterActive()) card.appendChild(el("div", { class: "meaning", text: brief.decision }));
      card.addEventListener("click", () => { const s = document.getElementById("section-" + c.id); if (s) s.scrollIntoView({ behavior: "smooth" }); });
      host.appendChild(card);
    }
  }

  // ------------------------------------------------------------ sections

  function buildSections() {
    const host = document.getElementById("sections");
    for (const sec of SECTIONS) {
      const section = el("section", { class: "section", id: "section-" + sec.id }, [el("h2", { text: sec.title })]);
      const q = Q[sec.charts[0]];
      if (q) section.appendChild(el("p", { class: "question", text: q.question }));
      for (const name of sec.charts) section.appendChild(el("div", { class: "chart", id: "chart-" + name }));
      if (sec.drill) section.appendChild(el("div", { id: "drill-paths" }));
      if (sec.queue) section.appendChild(el("div", { id: "open-queue" }));
      const brief = briefByName[sec.brief];
      const briefEl = el("div", { class: "brief", id: "brief-" + sec.id });
      if (brief) {
        briefEl.appendChild(el("div", { class: "headline", text: brief.headline }));
        if (brief.text) briefEl.appendChild(el("p", { class: "text", text: brief.text }));
        briefEl.appendChild(el("div", { class: "note", id: "note-" + sec.id }));
      }
      section.appendChild(briefEl);
      for (const extra of sec.extra || []) {
        const b = briefByName[extra];
        if (b) section.appendChild(el("div", { class: "brief" }, [el("div", { class: "headline", text: b.headline }), el("p", { class: "text", text: b.text }), el("p", { class: "decision", text: b.decision })]));
      }
      if (brief) section.appendChild(el("p", { class: "decision", text: brief.decision }));
      host.appendChild(section);
    }
  }

  function updateNotes() {
    const note = filterActive() ? "Numbers on the charts and cards are for the filtered scope. This text was written for the whole period." : "";
    for (const sec of SECTIONS) { const n = document.getElementById("note-" + sec.id); if (n) n.textContent = note; }
    const scope = document.getElementById("scope");
    const parts = [];
    if (state.from || state.to) parts.push(`${state.from || "start"} to ${state.to || "end"}`);
    for (const k of ["kind", "agent", "reviewer", "mode", "tag"]) if (state[k]) parts.push(`${k}: ${state[k]}`);
    scope.textContent = parts.length ? "Scope: " + parts.join(" · ") : "Scope: the whole chain";
    scope.className = "scope" + (parts.length ? " active" : "");
  }

  // ------------------------------------------------------------ charts

  const views = {};

  async function embed(name, spec) {
    const host = document.getElementById("chart-" + name);
    if (!host) return;
    try {
      const result = await vegaEmbed(host, spec, { actions: { export: true, source: false, compiled: false, editor: false }, renderer: "svg", logLevel: vega.Error });
      views[name] = result.view;
      if (name === "patch_heatmap") wireDrill(result.view);
      if (name === "rate_over_time") wireBrush(result.view);
    } catch (e) {
      host.textContent = "This chart could not be drawn: " + (e && e.message ? e.message : e);
    }
  }

  function specFor(name, F) {
    const spec = clone(T[name]);
    const builder = builders[name];
    if (!builder) return spec;
    const values = builder(F, spec);
    if (values.length === 0 && spec.layer) {
      // Keep the page readable when a filter empties a chart.
      return Object.assign(clone(T[name]), { data: { values: [] }, layer: undefined, mark: { type: "text", text: "No rows in this scope.", fontSize: 12, color: "#8a8f98" }, width: 420, height: 60, encoding: undefined, resolve: undefined });
    }
    spec.data = { values };
    return spec;
  }

  async function renderCharts(F, except) {
    for (const sec of SECTIONS) for (const name of sec.charts) {
      if (except && except.has(name)) continue;
      await embed(name, specFor(name, F));
    }
    renderQueue(F);
    renderDrill(F, null);
  }

  // Heatmap drill-through: the overrides behind the clicked cell.
  let drillCell = null;
  function wireDrill(view) {
    view.addEventListener("click", (event, item) => {
      if (!item || !item.datum || item.datum.top_path === undefined) return;
      drillCell = { top_path: item.datum.top_path, task_kind: item.datum.task_kind };
      renderDrill(filtered(), drillCell);
    });
  }

  function renderDrill(F, cell) {
    const host = document.getElementById("drill-paths");
    if (!host) return;
    host.innerHTML = "";
    if (!cell) { host.appendChild(el("p", { class: "drill-title", text: "Click a cell to see the corrections behind it and the reasons reviewers gave." })); return; }
    const rows = F.overrides.filter((o) => o.top_path === cell.top_path && o.task_kind === cell.task_kind);
    host.appendChild(el("p", { class: "drill-title", text: `${rows.length} correction${rows.length === 1 ? "" : "s"} to ${cell.top_path} in ${cell.task_kind} tasks` }));
    const table = el("table", { class: "drill" }, [el("tr", {}, ["When", "Reviewer", "Refined", "Tags", "Rationale"].map((h) => el("th", { text: h })))]);
    for (const o of rows.slice().sort((a, b) => (a.ts < b.ts ? -1 : 1))) {
      table.appendChild(el("tr", {}, [
        el("td", { text: (o.ts || "").slice(0, 16).replace("T", " ") }), el("td", { text: o.reviewer || "" }),
        el("td", { text: o.intent_preserved === true ? "yes" : o.intent_preserved === false ? "no" : "unstated" }),
        el("td", { text: (o.tags || []).join(", ") }), el("td", { text: o.rationale || "" }),
      ]));
    }
    host.appendChild(table);
  }

  function renderQueue(F) {
    const host = document.getElementById("open-queue");
    if (!host) return;
    host.innerHTML = "";
    const open = F.passes.filter((p) => !p.event).slice().sort((a, b) => b.duration_s - a.duration_s);
    if (!open.length) { host.appendChild(el("p", { class: "drill-title", text: "No review is waiting in this scope." })); return; }
    host.appendChild(el("p", { class: "drill-title", text: `${open.length} review${open.length === 1 ? "" : "s"} still waiting, oldest first` }));
    const table = el("table", { class: "drill" }, [el("tr", {}, ["Task", "Kind", "Assignee", "Requested", "Waiting"].map((h) => el("th", { text: h })))]);
    for (const p of open.slice(0, 25)) {
      table.appendChild(el("tr", {}, [el("td", { text: p.task_id }), el("td", { text: p.task_kind || "" }), el("td", { text: p.assignee || "" }),
        el("td", { text: (p.requested_at || "").slice(0, 16).replace("T", " ") }), el("td", { text: hours(p.duration_s) })]));
    }
    host.appendChild(table);
  }

  // Brushing the time axis on the first chart re-scopes the page.
  function wireBrush(view) {
    let timer = null;
    try {
      view.addSignalListener("brush", (name, value) => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          if (value && value.period && value.period.length === 2) {
            state.from = new Date(value.period[0]).toISOString().slice(0, 10);
            state.to = new Date(value.period[1]).toISOString().slice(0, 10);
          } else { state.from = ""; state.to = ""; }
          document.getElementById("f-from").value = state.from;
          document.getElementById("f-to").value = state.to;
          refresh(new Set(["rate_over_time"]));
        }, 150);
      });
    } catch (e) { /* the template lacks the brush parameter; the date inputs still work */ }
  }

  function addBrushParam() {
    const spec = T.rate_over_time;
    if (!spec || !spec.layer) return;
    const line = spec.layer[spec.layer.length - 1];
    line.params = [{ name: "brush", select: { type: "interval", encodings: ["x"] } }];
  }

  // ------------------------------------------------------------ lineage

  function buildLineageSelector() {
    const sel = document.getElementById("lineage-task");
    const tasks = D.tasks.slice().sort((a, b) => ((b.created_at || "") < (a.created_at || "") ? -1 : 1));
    for (const t of tasks) {
      if (!D.lineages[t.task_id]) continue;
      sel.appendChild(el("option", { value: t.task_id, text: `${t.task_id}  ·  ${t.kind || ""}  ·  ${t.outcome || ""}` }));
    }
    sel.addEventListener("change", () => renderLineage(sel.value));
    if (sel.options.length) renderLineage(sel.options[0].value);
  }

  function renderLineage(tid) {
    const rows = D.lineages[tid] || [];
    const spec = clone(T.lineage);
    spec.title.text = "Lineage of " + tid;
    spec.data = { values: rows };
    spec.width = Math.max(360, 60 * rows.length + 60);
    embed("lineage", spec);
  }

  // ------------------------------------------------------------ filters

  function fillOptions(id, values) {
    const sel = document.getElementById(id);
    for (const v of [...new Set(values.filter((x) => x))].sort()) sel.appendChild(el("option", { value: v, text: v }));
  }

  function buildFilters() {
    fillOptions("f-kind", D.tasks.map((t) => t.kind));
    fillOptions("f-agent", D.tasks.map((t) => t.assignee));
    fillOptions("f-reviewer", D.decisions.map((d) => d.reviewer));
    fillOptions("f-mode", D.tasks.map((t) => t.mode));
    fillOptions("f-tag", D.tasks.flatMap((t) => t.tags || []).concat(D.overrides.flatMap((o) => o.tags || [])));
    const bind = (id, key) => document.getElementById(id).addEventListener("change", (e) => { state[key] = e.target.value; refresh(); });
    bind("f-from", "from"); bind("f-to", "to"); bind("f-kind", "kind"); bind("f-agent", "agent");
    bind("f-reviewer", "reviewer"); bind("f-mode", "mode"); bind("f-tag", "tag");
    document.getElementById("f-clear").addEventListener("click", () => {
      for (const k of Object.keys(state)) state[k] = "";
      for (const id of ["f-from", "f-to", "f-kind", "f-agent", "f-reviewer", "f-mode", "f-tag"]) document.getElementById(id).value = "";
      refresh();
    });
  }

  async function refresh(except) {
    const F = filtered();
    updateNotes();
    renderHeadlines(F);
    await renderCharts(F, except);
  }

  // ------------------------------------------------------------ start

  addBrushParam();
  buildFilters();
  buildSections();
  buildLineageSelector();
  refresh();
})();
