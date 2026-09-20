// Runs the report's browser statistics under Node on the tables the report
// embeds, so the Python suite can compare them with chap_analytics.stats.
//   node js_stats_runner.js < embedded.json
"use strict";
const path = require("node:path");
const S = require(path.join(__dirname, "..", "chap_analytics", "_report", "stats.js"));
const chunks = [];
process.stdin.on("data", (c) => chunks.push(c));
process.stdin.on("end", () => {
  const D = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const hFor = (p0) => { let best = D.cusum_h[0]; for (const r of D.cusum_h) if (Math.abs(r.p0 - p0) < Math.abs(best.p0 - p0)) best = r; return best.h; };
  const out = {
    rates: S.rates(D.tasks),
    rates_by_kind: S.rates(D.tasks, "kind"),
    rates_by_reviewer: S.rates(D.tasks, "reviewer"),
    rates_over_time: S.ratesOverTime(D.tasks, D.meta.freq),
    refine_reverse: S.refineReverse(D.overrides),
    refine_by_kind: S.refineReverse(D.overrides, "task_kind"),
    patch_paths: S.patchPaths(D.patch_ops, "task_kind"),
    calibration: S.calibration(D.tasks, 10),
    survival: S.survival(D.passes),
    latency_by: S.latencyBy(D.passes, "reviewer"),
    promotion: S.promotion(D.tasks, D.meta.threshold, "assignee"),
    sequential_last: S.sequential(D.tasks, D.meta.threshold, Math.min(0.99, D.meta.threshold + 0.15), "assignee").slice(-1),
    agreement: S.agreement(D.decisions),
    pairwise: S.pairwiseAgreement(D.decisions),
    whispers: S.whispers(D.whispers, "asker"),
    handoffs: S.handoffs(D.handoffs, "recipient"),
    assurance: S.assurance(D.events, "D"),
    cusum: S.cusum(D.tasks, { shift: D.meta.shift, threshold: hFor }),
    coverage: S.coverage(D.tasks, D.decisions),
    concentration: S.concentration(D.decisions),
    duties: S.duties(D.tasks, D.decisions, D.handoffs),
  };
  process.stdout.write(JSON.stringify(out));
});
