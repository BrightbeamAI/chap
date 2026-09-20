# Changelog

`chap-analytics` versions on its own track. It is a reader of the protocol,
so a protocol release moves the coordinator packages together and leaves this
one where it is until the tables or the analyses change.

## 0.2.0

The decision layer: every row of the roadmap's decision table, as a
statistic with its interval, a chart, and a brief, assembled into a
self-contained interactive report.

- `chap_analytics.stats`: approval, override, rejection and abstention rates
  with Wilson intervals, overall, by group and over time; the refining
  against reversing split; patch-path frequency with the rationales behind
  each cell; the reliability table with expected calibration error and Brier
  score; time to decision as a Kaplan-Meier curve with open reviews
  censored, latency by group and the open queue; promotion readiness as a
  Beta posterior against a threshold and Wald's sequential test; Fleiss'
  kappa on multi-reviewer passes and on deliberation votes, pairwise Cohen's
  kappa and abstentions by category; whisper lapse and handoff acceptance
  rates with response times; chain assurance per period; and a Bernoulli
  CUSUM on the correction rate with its decision interval set by simulation
  for a stated false-alarm rate. Every estimate with a minimum carries a
  `sufficient` flag and the minimum it was judged against.
- `chap_analytics.graph`: the chain as a typed graph with a fixed ontology
  of nine node types and twenty-five edge types; the lineage of one task as
  a graph and as swimlane rows; the collaboration graph with degree,
  betweenness and per-agent reviewer concentration; oversight coverage of
  shipped outcomes; separation-of-duties findings; a Fruchterman-Reingold
  layout; node-link JSON, GraphML and networkx exports; and the ontology
  drawn as SVG from the declarations.
- `chap_analytics.charts`: one Vega-Lite specification per chart in the
  decision table, as a plain dictionary that a notebook renders, Altair
  takes, `Chart.save` writes as SVG or PNG, and the report embeds. Charts
  carry the question they answer and the decision they inform.
- `chap_analytics.briefs`: each decision in a paragraph, with the numbers,
  their intervals, and the decision they support, in words that change with
  the numbers.
- `chap_analytics.report`: one HTML file with the Vega runtime, the rows
  behind every chart and the page inside it. Headline cards per decision, a
  filter bar for date range, kind, agent, reviewer, mode and tag, brushing
  on the time axis, drill-through from the correction heatmap to the
  rationales, the open queue, and a lineage view for any task. Every
  statistic is recomputed in the browser as the reader filters, by a
  JavaScript mirror of the library that the suite checks against the Python
  functions. Artefact content stays out of the file.
- `chap_analytics.watch`: a `Watcher` that reads `audit.read` incrementally
  from a sequence cursor over HTTP, an in-process coordinator or any
  callable, re-projects, regenerates the report, and raises each drift alarm
  once with the tasks behind it.
- `chap_analytics.export`: evaluation cases with the agent's output, the
  corrected output and the rationale, written as JSON lines; prompt-revision
  candidates ranked by frequency and reversing share; and routing
  calibration, the confidence threshold that reaches a target acceptance.
- `chap_analytics.models`: reviewer severity separated from agent quality
  by a Rasch-style logistic model in numpy, with approximate standard
  errors, which declines to fit below its minimum and says why.
- `chap_analytics.sample.synthetic`: a workspace generated from stated
  rates (override, rejection, refining share, quorum share and agreement,
  latency, open share, whisper and lapse rates, handoffs, drift, reviewer
  strictness, agent quality, and how confidence relates to outcomes), so
  every statistic is tested against the truth that produced it. The
  coordinator's own clock follows the simulated timeline.
- `events.signed` and `events.scitt_submitted` columns.
- A `concerns` edge for the task a deliberation is about; `asked_to` and
  `whispered_to` read from the `to` field of the request envelope; `fulfils`
  read from the `fulfils` field of a `task.complete` envelope where a chain
  carries one.
- Documentation: the README carries the decision table and the pictures;
  `docs/methods.md`, `docs/graph.md` and `docs/report.md` state each
  method's assumptions and minimums, the ontology, and how the report is
  built. The walkthrough notebook continues from the tables to the decision
  layer, with the charts drawn inline.
- Extras: `viz` (Altair and vl-convert), `graph` (networkx). numpy is named
  as a dependency alongside pandas.

## 0.1.0

First release: stage one of the analytics roadmap.

- Eleven documented tables, projected from a CHAP audit chain by replaying
  the envelope stream: `events`, `tasks`, `decisions`, `overrides`,
  `patch_ops`, `participants`, `deliberations`, `votes`, `whispers`,
  `handoffs`, `routing`. Every column declared with its dtype and provenance.
- Four loaders: a SQLite store, a JSON export, an `audit.read` endpoint, and
  a `Coordinator` in the same process.
- Review passes kept apart, so a task sent back for revision is measured from
  each pass's own opening and `outcome` reads from the decision that settled
  the last one.
- The corrected artefact reconstructed from the patch with the coordinator's
  own RFC 6902 semantics, and checked against what the coordinator stored by
  the differential suite.
- Successors minted by escalation and supersession counted as tasks, with
  what they inherit from the original.
- `id_certain` and `assignee_certain`, so a row says which of its values are
  inferred.
- A redactor hook that covers every artefact-bearing field, in both the
  envelope stream and the snapshot.
- `chap_analytics.sample.support_desk()`, a generated week of review work; a
  walkthrough notebook and a printed report over it.
- A differential suite: random workspaces against a live coordinator, read
  both ways and checked against what the coordinator holds.
