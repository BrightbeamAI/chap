// The statistics the report recomputes in the browser when a filter changes.
//
// Each function mirrors the function of the same name in chap_analytics.stats
// and takes the row-level tables the report embeds. A test in the Python suite
// runs these under Node on the same tables and requires the results to match
// the Python ones, so the numbers on the page are the numbers in the library.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.CHAP_STATS = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const Z = 1.959963984540054;
  const DECIDED = new Set(["approved", "overridden", "rejected", "abstained"]);

  // ------------------------------------------------------------ intervals

  function wilson(k, n) {
    if (!(n > 0)) return [NaN, NaN];
    const p = k / n;
    const denom = 1 + (Z * Z) / n;
    const centre = (p + (Z * Z) / (2 * n)) / denom;
    const half = (Z * Math.sqrt((p * (1 - p)) / n + (Z * Z) / (4 * n * n))) / denom;
    return [Math.max(0, centre - half), Math.min(1, centre + half)];
  }

  function lgamma(x) {
    // Lanczos approximation, adequate to ~1e-13 for x > 0.
    const g = 7;
    const c = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313,
      -176.61502916214059, 12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6,
      1.5056327351493116e-7];
    if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
    x -= 1;
    let a = c[0];
    const t = x + g + 0.5;
    for (let i = 1; i < g + 2; i++) a += c[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
  }

  function betacf(a, b, x) {
    const tiny = 1e-300;
    const qab = a + b, qap = a + 1, qam = a - 1;
    let c = 1, d = 1 - (qab * x) / qap;
    d = 1 / (Math.abs(d) > tiny ? d : tiny);
    let h = d;
    for (let m = 1; m < 300; m++) {
      const m2 = 2 * m;
      let aa = (m * (b - m) * x) / ((qam + m2) * (a + m2));
      d = 1 + aa * d; d = 1 / (Math.abs(d) > tiny ? d : tiny);
      c = 1 + aa / (Math.abs(c) > tiny ? c : tiny);
      h *= d * c;
      aa = (-(a + m) * (qab + m) * x) / ((a + m2) * (qap + m2));
      d = 1 + aa * d; d = 1 / (Math.abs(d) > tiny ? d : tiny);
      c = 1 + aa / (Math.abs(c) > tiny ? c : tiny);
      const delta = d * c;
      h *= delta;
      if (Math.abs(delta - 1) < 3e-14) break;
    }
    return h;
  }

  function betaCdf(x, a, b) {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    const lbeta = lgamma(a + b) - lgamma(a) - lgamma(b);
    const front = Math.exp(lbeta + a * Math.log(x) + b * Math.log(1 - x));
    if (x < (a + 1) / (a + b + 2)) return (front * betacf(a, b, x)) / a;
    return 1 - (front * betacf(b, a, 1 - x)) / b;
  }

  function betaQuantile(q, a, b) {
    let lo = 0, hi = 1;
    for (let i = 0; i < 80; i++) {
      const mid = (lo + hi) / 2;
      if (betaCdf(mid, a, b) < q) lo = mid; else hi = mid;
    }
    return (lo + hi) / 2;
  }

  // ------------------------------------------------------------ helpers

  function groupBy(rows, key) {
    const out = new Map();
    for (const r of rows) {
      const k = typeof key === "function" ? key(r) : r[key];
      const kk = k === undefined || k === null ? null : k;
      if (!out.has(kk)) out.set(kk, []);
      out.get(kk).push(r);
    }
    return out;
  }

  function median(values) {
    const v = values.filter((x) => x !== null && x !== undefined && !Number.isNaN(x)).sort((a, b) => a - b);
    if (!v.length) return NaN;
    const mid = Math.floor(v.length / 2);
    return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
  }

  function quantile(values, q) {
    // pandas' default, linear interpolation.
    const v = values.filter((x) => x !== null && x !== undefined && !Number.isNaN(x)).sort((a, b) => a - b);
    if (!v.length) return NaN;
    const pos = (v.length - 1) * q;
    const lo = Math.floor(pos), hi = Math.ceil(pos);
    return v[lo] + (v[hi] - v[lo]) * (pos - lo);
  }

  function mean(values) {
    const v = values.filter((x) => x !== null && x !== undefined && !Number.isNaN(x));
    return v.length ? v.reduce((a, b) => a + b, 0) / v.length : NaN;
  }

  function decidedTasks(tasks) {
    return tasks.filter((t) => DECIDED.has(t.outcome));
  }

  // Period start in UTC, matching pandas to_period(...).start_time for D, W, M.
  function periodStart(iso, freq) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    const y = d.getUTCFullYear(), m = d.getUTCMonth(), day = d.getUTCDate();
    if (freq === "D") return new Date(Date.UTC(y, m, day)).toISOString();
    if (freq === "M" || freq === "MS") return new Date(Date.UTC(y, m, 1)).toISOString();
    // W: weeks run Monday to Sunday.
    const dow = (d.getUTCDay() + 6) % 7;
    return new Date(Date.UTC(y, m, day - dow)).toISOString();
  }

  // ------------------------------------------------------------ rates

  const RATE_OUTCOMES = [["approve", "approved"], ["override", "overridden"], ["reject", "rejected"], ["abstain", "abstained"]];

  function rates(tasks, by, minimum) {
    minimum = minimum === undefined ? 10 : minimum;
    const decided = decidedTasks(tasks);
    const groups = by ? groupBy(decided, by) : new Map([[null, decided]]);
    const out = [];
    for (const [key, rows] of groups) {
      const row = { n: rows.length, sufficient: rows.length >= minimum };
      if (by) row[by] = key;
      for (const [name, outcome] of RATE_OUTCOMES) {
        const k = rows.filter((r) => r.outcome === outcome).length;
        row[outcome] = k;
        row[name + "_rate"] = k / rows.length;
        const [lo, hi] = wilson(k, rows.length);
        row[name + "_low"] = lo; row[name + "_high"] = hi;
      }
      out.push(row);
    }
    return out;
  }

  function ratesOverTime(tasks, freq, minimum) {
    minimum = minimum === undefined ? 10 : minimum;
    const decided = decidedTasks(tasks).filter((t) => t.settled_at);
    const groups = groupBy(decided, (t) => periodStart(t.settled_at, freq));
    const out = [];
    for (const [period, rows] of [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
      const row = { period: period, n: rows.length, sufficient: rows.length >= minimum };
      for (const [name, outcome] of RATE_OUTCOMES) {
        const k = rows.filter((r) => r.outcome === outcome).length;
        row[name + "_rate"] = k / rows.length;
        const [lo, hi] = wilson(k, rows.length);
        row[name + "_low"] = lo; row[name + "_high"] = hi;
      }
      out.push(row);
    }
    return out;
  }

  function refineReverse(overrides, by, minimum) {
    minimum = minimum === undefined ? 10 : minimum;
    const groups = by ? groupBy(overrides, by) : new Map([[null, overrides]]);
    const out = [];
    for (const [key, rows] of groups) {
      const refining = rows.filter((r) => r.intent_preserved === true).length;
      const reversing = rows.filter((r) => r.intent_preserved === false).length;
      const stated = refining + reversing;
      const [lo, hi] = wilson(refining, stated);
      const row = { n_overrides: rows.length, refining, reversing, unstated: rows.length - stated,
        refine_share: stated > 0 ? refining / stated : NaN, low: lo, high: hi, sufficient: stated >= minimum };
      if (by) row[by] = key;
      out.push(row);
    }
    return out;
  }

  // ------------------------------------------------------------ paths

  function patchPaths(ops, by) {
    by = by === undefined ? "task_kind" : by;
    // Distinct (group, task_id, seq, top_path).
    const seen = new Set();
    const touched = [];
    for (const o of ops) {
      const k = [by ? o[by] : "", o.task_id, o.seq, o.top_path].join("");
      if (seen.has(k)) continue;
      seen.add(k);
      touched.push(o);
    }
    const perGroup = new Map();
    for (const o of touched) {
      const g = by ? o[by] : null;
      if (!perGroup.has(g)) perGroup.set(g, new Set());
      perGroup.get(g).add(o.task_id + "" + o.seq);
    }
    const counts = new Map();
    for (const o of touched) {
      const g = by ? o[by] : null;
      const k = g + "" + o.top_path;
      if (!counts.has(k)) counts.set(k, { group: g, top_path: o.top_path, n: 0 });
      counts.get(k).n += 1;
    }
    const out = [];
    for (const c of counts.values()) {
      const total = perGroup.get(c.group).size;
      const row = { top_path: c.top_path, n: c.n, overrides: total, share: c.n / total };
      if (by) row[by] = c.group;
      out.push(row);
    }
    out.sort((a, b) => (by && a[by] !== b[by] ? (a[by] < b[by] ? -1 : 1) : b.n - a.n));
    return out;
  }

  // ------------------------------------------------------------ calibration

  function calibration(tasks, bins, minimum) {
    bins = bins || 10; minimum = minimum === undefined ? 30 : minimum;
    const rows = decidedTasks(tasks).filter((t) => t.confidence !== null && t.confidence !== undefined &&
      ["approved", "overridden", "rejected"].includes(t.outcome));
    const step = 1 / bins;
    const edges = [];
    for (let i = 1; i < bins; i++) edges.push(i * step);
    const table = [];
    for (let b = 0; b < bins; b++) table.push({ bin: b, low_edge: b * step, high_edge: (b + 1) * step, n: 0, accepted: 0, sumConf: 0 });
    for (const t of rows) {
      let idx = 0;
      for (const e of edges) if (e < t.confidence) idx += 1;
      idx = Math.min(bins - 1, Math.max(0, idx));
      const cell = table[idx];
      cell.n += 1; cell.sumConf += t.confidence;
      if (t.outcome === "approved") cell.accepted += 1;
    }
    let ece = 0, brier = 0;
    const n = rows.length;
    for (const cell of table) {
      cell.mean_confidence = cell.n ? cell.sumConf / cell.n : NaN;
      cell.acceptance = cell.n ? cell.accepted / cell.n : NaN;
      const [lo, hi] = wilson(cell.accepted, cell.n);
      cell.low = lo; cell.high = hi;
      delete cell.sumConf;
      if (cell.n) ece += (cell.n / n) * Math.abs(cell.acceptance - cell.mean_confidence);
    }
    for (const t of rows) brier += Math.pow(t.confidence - (t.outcome === "approved" ? 1 : 0), 2);
    return { table, n, ece: n ? ece : NaN, brier: n ? brier / n : NaN, sufficient: n >= minimum };
  }

  // ------------------------------------------------------------ latency

  function survival(passes, by) {
    const groups = by ? groupBy(passes, by) : new Map([[null, passes]]);
    const out = [];
    for (const [key, rowsAll] of groups) {
      const rows = rowsAll.filter((p) => p.duration_s !== null && p.duration_s !== undefined).sort((a, b) => a.duration_s - b.duration_s);
      let s = 1, varSum = 0;
      const times = [...new Set(rows.map((r) => r.duration_s))].sort((a, b) => a - b);
      for (const t of times) {
        const atRisk = rows.filter((r) => r.duration_s >= t).length;
        const here = rows.filter((r) => r.duration_s === t);
        const d = here.filter((r) => r.event).length;
        const c = here.length - d;
        if (d > 0 && atRisk > 0) {
          s *= 1 - d / atRisk;
          if (atRisk > d) varSum += d / (atRisk * (atRisk - d));
        }
        const se = s > 0 ? s * Math.sqrt(varSum) : 0;
        const row = { time_s: t, at_risk: atRisk, events: d, censored: c, survival: s,
          low: Math.max(0, s - Z * se), high: Math.min(1, s + Z * se) };
        if (by) row[by] = key;
        out.push(row);
      }
    }
    return out;
  }

  function latencyBy(passes, by, minimum) {
    minimum = minimum === undefined ? 5 : minimum;
    const decided = passes.filter((p) => p.event);
    const opened = passes.filter((p) => !p.event);
    const groups = groupBy(decided, by);
    const openGroups = groupBy(opened, by);
    const keys = new Set([...groups.keys(), ...openGroups.keys()]);
    const out = [];
    for (const key of keys) {
      const rows = groups.get(key) || [];
      const d = rows.map((r) => r.duration_s);
      const row = { n: rows.length, open: (openGroups.get(key) || []).length,
        median_s: median(d), p90_s: quantile(d, 0.9), mean_s: mean(d), sufficient: rows.length >= minimum };
      row[by] = key;
      out.push(row);
    }
    return out;
  }

  // ------------------------------------------------------------ promotion

  function promotion(tasks, threshold, by, opts) {
    opts = opts || {};
    const prior = opts.prior || [1, 1], credible = opts.credible || 0.9, minimum = opts.minimum === undefined ? 20 : opts.minimum;
    let decided = decidedTasks(tasks);
    if (opts.mode) decided = decided.filter((t) => t.mode === opts.mode);
    const groups = by ? groupBy(decided, by) : new Map([[null, decided]]);
    const out = [];
    for (const [key, rows] of groups) {
      const n = rows.length;
      const k = rows.filter((t) => t.against).length;
      const a = prior[0] + k, b = prior[1] + n - k;
      const row = { n, against: k, rate: n ? k / n : NaN, posterior_mean: a / (a + b),
        low: betaQuantile((1 - credible) / 2, a, b), high: betaQuantile(1 - (1 - credible) / 2, a, b),
        p_below_threshold: betaCdf(threshold, a, b), threshold, alpha: a, beta: b, sufficient: n >= minimum };
      if (by) row[by] = key;
      out.push(row);
    }
    return out;
  }

  function sequential(tasks, p0, p1, by, opts) {
    opts = opts || {};
    const alpha = opts.alpha || 0.05, beta = opts.beta || 0.2;
    const decided = decidedTasks(tasks).filter((t) => t.settled_at).sort((a, b) => (a.settled_at < b.settled_at ? -1 : a.settled_at > b.settled_at ? 1 : 0));
    const upper = Math.log((1 - beta) / alpha), lower = Math.log(beta / (1 - alpha));
    const incBad = Math.log(p1 / p0), incGood = Math.log((1 - p1) / (1 - p0));
    const groups = by ? groupBy(decided, by) : new Map([[null, decided]]);
    const out = [];
    for (const [key, rows] of groups) {
      let llr = 0, verdict = "continue", i = 0;
      for (const t of rows) {
        i += 1;
        if (verdict === "continue") {
          llr += t.against ? incBad : incGood;
          if (llr >= upper) verdict = "hold"; else if (llr <= lower) verdict = "promote";
        }
        const row = { task_id: t.task_id, settled_at: t.settled_at, i, against: !!t.against, llr, upper, lower, verdict };
        if (by) row[by] = key;
        out.push(row);
      }
    }
    return out;
  }

  // ------------------------------------------------------------ agreement

  function multiDecidedPasses(decisions) {
    const d = decisions.filter((x) => ["approve", "override", "reject"].includes(x.kind))
      .map((x) => Object.assign({}, x, { category: x.kind === "approve" ? "accept" : "change" }));
    const byPass = groupBy(d, (x) => x.task_id + "" + x.review_index);
    const out = [];
    for (const rows of byPass.values()) {
      const reviewers = new Set(rows.map((r) => r.reviewer));
      if (reviewers.size >= 2) out.push(rows);
    }
    return out;
  }

  function lastPerReviewer(rows) {
    const last = new Map();
    for (const r of [...rows].sort((a, b) => a.seq - b.seq)) last.set(r.reviewer, r);
    return [...last.values()];
  }

  function agreement(decisions, minimum) {
    minimum = minimum === undefined ? 10 : minimum;
    const passes = multiDecidedPasses(decisions).map(lastPerReviewer);
    const bySize = groupBy(passes, (p) => p.length);
    const out = [];
    for (const m of [...bySize.keys()].sort((a, b) => a - b)) {
      const subs = bySize.get(m);
      const counts = subs.map((p) => {
        const acc = p.filter((r) => r.category === "accept").length;
        return [acc, p.length - acc];
      });
      const nSub = counts.length;
      const pI = counts.map((c) => (c[0] * (c[0] - 1) + c[1] * (c[1] - 1)) / (m * (m - 1)));
      const pBar = mean(pI);
      const totals = [0, 0];
      for (const c of counts) { totals[0] += c[0]; totals[1] += c[1]; }
      const pJ = totals.map((t) => t / (nSub * m));
      const pE = pJ[0] * pJ[0] + pJ[1] * pJ[1];
      const reviewers = new Set();
      for (const p of subs) for (const r of p) reviewers.add(r.reviewer);
      out.push({ raters: m, n_passes: nSub, n_reviewers: reviewers.size, agreement_observed: pBar,
        agreement_expected: pE, kappa: pE < 1 ? (pBar - pE) / (1 - pE) : NaN, sufficient: nSub >= minimum });
    }
    return out;
  }

  function pairwiseAgreement(decisions, minimum) {
    minimum = minimum === undefined ? 10 : minimum;
    const passes = multiDecidedPasses(decisions).map(lastPerReviewer);
    const reviewers = [...new Set(passes.flat().map((r) => r.reviewer))].sort();
    const out = [];
    for (let i = 0; i < reviewers.length; i++) {
      for (let j = i + 1; j < reviewers.length; j++) {
        const a = reviewers[i], b = reviewers[j];
        const both = [];
        for (const p of passes) {
          const ra = p.find((r) => r.reviewer === a), rb = p.find((r) => r.reviewer === b);
          if (ra && rb) both.push([ra.category, rb.category]);
        }
        const n = both.length;
        if (!n) continue;
        const po = both.filter((x) => x[0] === x[1]).length / n;
        const pa = { accept: both.filter((x) => x[0] === "accept").length / n, change: both.filter((x) => x[0] === "change").length / n };
        const pb = { accept: both.filter((x) => x[1] === "accept").length / n, change: both.filter((x) => x[1] === "change").length / n };
        const pe = pa.accept * pb.accept + pa.change * pb.change;
        out.push({ reviewer_a: a, reviewer_b: b, n_passes: n, agreement_observed: po,
          kappa: pe < 1 ? (po - pe) / (1 - pe) : NaN, sufficient: n >= minimum });
      }
    }
    return out;
  }

  // ------------------------------------------------------------ whispers, handoffs

  function whispers(rows, by, minimum) {
    minimum = minimum === undefined ? 5 : minimum;
    const groups = by ? groupBy(rows, by) : new Map([[null, rows]]);
    const out = [];
    for (const [key, ws] of groups) {
      const answered = ws.filter((w) => w.answered === true).length;
      const lapsed = ws.filter((w) => w.lapsed === true).length;
      const pending = ws.filter((w) => w.state === "pending").length;
      const resolved = ws.length - pending;
      const [lo, hi] = wilson(lapsed, resolved);
      const row = { n: ws.length, answered, lapsed, pending, lapse_rate: resolved > 0 ? lapsed / resolved : NaN,
        low: lo, high: hi, median_response_s: median(ws.map((w) => w.response_s)), sufficient: resolved >= minimum };
      if (by) row[by] = key;
      out.push(row);
    }
    return out;
  }

  function handoffs(rows, by, minimum) {
    minimum = minimum === undefined ? 5 : minimum;
    const groups = by ? groupBy(rows, by) : new Map([[null, rows]]);
    const out = [];
    for (const [key, hs] of groups) {
      const accepted = hs.filter((h) => h.resolution === "accepted").length;
      const declined = hs.filter((h) => h.resolution === "declined").length;
      const open = hs.filter((h) => h.resolution === "open").length;
      const resolved = accepted + declined;
      const [lo, hi] = wilson(accepted, resolved);
      const row = { n: hs.length, accepted, declined, open, accept_rate: resolved > 0 ? accepted / resolved : NaN,
        low: lo, high: hi, median_response_s: median(hs.map((h) => h.response_s)), sufficient: resolved >= minimum };
      if (by) row[by] = key;
      out.push(row);
    }
    return out;
  }

  // ------------------------------------------------------------ assurance

  function assurance(events, freq) {
    freq = freq || "D";
    const groups = groupBy(events.filter((e) => e.ts), (e) => periodStart(e.ts, freq));
    const out = [];
    for (const [period, rows] of [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1))) {
      const n = rows.length;
      const chained = rows.filter((r) => r.chained === true).length;
      const signed = rows.filter((r) => r.signed === true).length;
      const sub = rows.filter((r) => r.scitt_submitted === true).length;
      out.push({ period, n, chained, signed, scitt_submitted: sub, chained_share: chained / n, signed_share: signed / n, scitt_share: sub / n });
    }
    return out;
  }

  // ------------------------------------------------------------ drift

  function cusum(tasks, opts) {
    opts = opts || {};
    const shift = opts.shift === undefined ? 0.1 : opts.shift, baseline = opts.baseline || 100;
    const decided = decidedTasks(tasks).filter((t) => t.settled_at).sort((a, b) => (a.settled_at < b.settled_at ? -1 : a.settled_at > b.settled_at ? 1 : 0));
    if (!decided.length) return [];
    const changed = decided.map((t) => t.outcome === "overridden" || t.outcome === "rejected");
    let p0 = opts.target !== undefined && opts.target !== null ? opts.target : mean(changed.slice(0, baseline).map((x) => (x ? 1 : 0)));
    p0 = Math.min(Math.max(p0, 0.02), 0.98);
    const p1 = Math.min(p0 + shift, 0.99);
    const incBad = Math.log(p1 / p0), incGood = Math.log((1 - p1) / (1 - p0));
    const h = typeof opts.threshold === "function" ? opts.threshold(p0) : opts.threshold;
    let s = 0, running = 0;
    const out = [];
    decided.forEach((t, idx) => {
      s = Math.max(0, s + (changed[idx] ? incBad : incGood));
      running += changed[idx] ? 1 : 0;
      const alarm = s >= h;
      out.push({ task_id: t.task_id, settled_at: t.settled_at, i: idx + 1, changed: changed[idx],
        rate_so_far: running / (idx + 1), statistic: s, threshold: h, alarm, target: p0, detect: p1 });
      if (alarm) s = 0;
    });
    return out;
  }

  // ------------------------------------------------------------ graph findings

  const SHIPPED = new Set(["approved", "overridden", "completed_after_rejection", "completed_bypassing_review", "completed_without_review"]);

  function kind(uri) {
    return typeof uri === "string" && uri.includes(":") ? uri.split(":")[0] : null;
  }

  function coverage(tasks, decisions) {
    const humanDecided = new Set(decisions.filter((d) => kind(d.reviewer) === "human").map((d) => d.task_id));
    const supersedes = new Map(tasks.map((t) => [t.task_id, t.supersedes]));
    let shipped = 0, covered = 0;
    const uncovered = [];
    for (const t of tasks) {
      const isShipped = SHIPPED.has(t.outcome);
      const onTask = humanDecided.has(t.task_id);
      let onPred = false, prev = supersedes.get(t.task_id), hops = 0;
      while (typeof prev === "string" && hops < 100) {
        if (humanDecided.has(prev)) { onPred = true; break; }
        prev = supersedes.get(prev); hops += 1;
      }
      const performed = kind(t.assignee) === "human";
      if (isShipped) {
        shipped += 1;
        if (onTask || onPred || performed) covered += 1; else uncovered.push(t.task_id);
      }
    }
    return { shipped, covered, share: shipped ? covered / shipped : NaN, uncovered };
  }

  function concentration(decisions, minimum) {
    minimum = minimum === undefined ? 10 : minimum;
    const groups = groupBy(decisions.filter((d) => d.reviewer && d.assignee), "assignee");
    const out = [];
    for (const [assignee, rows] of groups) {
      const counts = new Map();
      for (const r of rows) counts.set(r.reviewer, (counts.get(r.reviewer) || 0) + 1);
      const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
      const total = rows.length;
      let hhi = 0;
      for (const [, c] of sorted) hhi += Math.pow(c / total, 2);
      out.push({ assignee, n_decisions: total, n_reviewers: counts.size, top_reviewer: sorted[0][0],
        top_share: sorted[0][1] / total, hhi, sufficient: total >= minimum });
    }
    out.sort((a, b) => b.top_share - a.top_share);
    return out;
  }

  function duties(tasks, decisions, handoffRows) {
    const byId = new Map(tasks.map((t) => [t.task_id, t]));
    const out = [];
    for (const d of decisions) {
      const t = byId.get(d.task_id);
      if (t) {
        if (d.reviewer && d.reviewer === t.assignee) out.push({ check: "self_review", subject: d.task_id, actor: d.reviewer });
        if (d.reviewer && d.reviewer === t.delegator) out.push({ check: "delegator_review", subject: d.task_id, actor: d.reviewer });
      }
      const k = kind(d.reviewer);
      if (k === "agent" || k === "service") out.push({ check: "agent_decided", subject: d.task_id, actor: d.reviewer });
    }
    for (const h of handoffRows || []) {
      if (h.resolved_by && h.resolved_by === h.proposer) out.push({ check: "self_handoff", subject: h.handoff_id, actor: h.proposer });
    }
    return out;
  }

  return {
    wilson, betaCdf, betaQuantile, lgamma, periodStart, median, quantile, mean,
    rates, ratesOverTime, refineReverse, patchPaths, calibration, survival, latencyBy,
    promotion, sequential, agreement, pairwiseAgreement, whispers, handoffs, assurance,
    cusum, coverage, concentration, duties, decidedTasks, DECIDED,
  };
});
