# CHAP analytics roadmap

> The record of a collaboration between people and agents, measured and drawn.

CHAP records what people decided about agent work. This is the plan for the
layer that reads those records back, measures them, draws them, and puts a
number and a chart next to each decision a team has to make about its agents.
Its job is to show what a workspace gets from running its human and agent
work through the protocol: who changed what, who decided, how long it took,
where the work moved, and whether the record holds up.

## The data

Every override on a CHAP chain carries the RFC 6902 patch that changed the
draft and a rationale in the reviewer's words. It may carry policy references,
workspace-defined tags, and a flag saying whether the reviewer refined the
agent's decision or reversed it. The chain around it carries the artefact it
started from. Every decision carries a reviewer, a kind, a rule and a
timestamp. Every task carries its assignee, its mode and its state history,
and the routing hints it was given. Every whisper, handoff, deliberation and
control operation is an envelope in the same log.

That is a human-labelled evaluation set with provenance and counterfactuals,
produced as a side effect of ordinary review, in one log, already joined. Any
other route to the same dataset instruments each application separately and
reconciles logs kept for other purposes. The layer treats the chain as the
dataset it already is.

### Two ways to read a chain

| Source | Carries |
|---|---|
| `audit.read`, over MCP or HTTP | Every envelope: all request parameters, in order, hash-linked where the coordinator chains its log. The audit log is what an MCP client can obtain. |
| A `SqliteStore` file or workspace snapshot | The full state of the workspace, and the audit log beside it. |

The tables are replayed from the envelope stream, so the envelope stream is
sufficient for most analyses. `review.request` carries the artefact under
review and `decide.override` carries the patch, so the before and the after of
every correction come from the envelopes themselves. The snapshot adds what
the server computed, which is deliberation and routing outcomes, and settles
which server-minted id belongs to which creation. Where both are available,
the stored values are used and the differential suite checks the replayed
ones against them.

## What the chain makes decidable

The layer is organised around decisions a team makes about its agents. Each
row is one question the chain answers, the statistic that answers it, the
chart that shows it, and the tables it reads.

| Decision | Question | Statistic | Chart | Tables |
|---|---|---|---|---|
| Revise the prompt, or revise the policy | How often do reviewers change the agent's output, and do they refine it or reverse it? | Override, rejection and approval rates with Wilson intervals; refining share from `intent_preserved` | Rate over time with its interval band; refine and reverse split by task kind and tag | `tasks`, `overrides`, `decisions` |
| Which part of the prompt to fix | Where do the corrections land in the artefact? | Patch path frequency by task kind, weighted by override count | Heatmap of `top_path` against kind, with the rationales behind each cell one click away | `patch_ops`, `overrides` |
| Retune the routing thresholds | Does the agent's reported confidence track its outcomes? | Reliability curve, expected calibration error, Brier score | Reliability diagram with per-bin counts | `tasks`, `decisions` |
| Promote an agent from trial to production | Is the reversing-override rate below the bar, given how many tasks have been seen? | Beta posterior probability that the true rate is under the threshold; a sequential test that says when enough has been seen | Posterior against the threshold as tasks accumulate; trial against production, side by side | `tasks`, `overrides` |
| Add reviewers, or reroute | Where do decisions wait, and for whom? | Time to decision as a survival curve with open reviews censored; latency by reviewer and kind; age of the open queue | Survival curve; latency distribution per reviewer; the open queue as a list | `decisions`, `tasks` |
| Rewrite an ambiguous policy | Do reviewers agree with each other on the same artefact? | Fleiss' kappa across `quorum:N` and `all_approve` reviews; abstention by category | Agreement matrix; abstentions by policy reference | `decisions`, `deliberations`, `votes` |
| Clarify the task inputs | How often do agents have to ask, and does anyone answer in time? | Whispers per task, lapse rate, response time | Lapse rate by asker and kind; response time distribution | `whispers` |
| Cover the shift | Are handoffs accepted, and how fast? | Acceptance rate, response time, declines by recipient | Handoff timeline with acceptances and declines | `handoffs` |
| Trust the record | Is the chain verifiable end to end? | Chained fraction, signed fraction, receipt coverage | Coverage over time | `events` |
| Notice a change early | Has the override rate moved since the last prompt or model change? | CUSUM on the override rate with a stated false-alarm rate | Control chart with the alarm marked | `tasks`, `overrides` |
| Trace one outcome to its origin | What led to this action, and who was involved at each step? | The lineage of one artefact: draft, review, decisions, override, supersession, escalation, execution, in order | A swimlane, one lane per participant, events as nodes, relations as edges | all tables, through the graph |
| Find the bottleneck | Who does most of the reviewing, and does one reviewer decide most of one agent's work? | Degree and betweenness on the collaboration graph; share of each agent's decisions taken by its top reviewer | Collaboration graph, humans and agents, edges weighted by decisions, sized by latency | `decisions`, `handoffs`, `participants` |
| Confirm there is a person in the loop | Which executed outcomes have a human decision somewhere on their path? | Fraction of executed outcomes whose lineage includes a human decision; the agent-only paths listed | Lineage graph with human decisions marked; the uncovered paths as a list | `tasks`, `decisions`, `overrides`, `handoffs` |
| Separate duties | Does the same actor draft and approve, propose and accept, or review its own work? | Count of same-actor pairs across roles, per workspace | Self-loops and two-node cycles highlighted on the collaboration graph | `tasks`, `decisions`, `handoffs`, `votes` |

Each statistic carries its sample size and its interval. Each chart is a
Vega-Lite specification produced from a frame, so the same chart renders in a
notebook, in the standalone report, and as a static image for a paper.

### The chain as a graph

The last four rows read the chain as a graph. Participants, tasks, artefacts,
reviews, whispers, deliberations and handoffs are nodes. Delegates, assigned
to, reviewed, decided, overrode (`based_on`), fulfils, supersedes, escalated
to, handed off to, asked, answered, voted in, and the `prev_hash` link between
envelopes are edges. Every edge is an envelope or a field on one, so the graph
is a second projection of the same data as the tables and it carries the same
`id_certain` marks.

Two things come out of it. The lineage of one outcome, which is the picture
an auditor asks for first: everything that led to this action, in order, with
the people and agents at each step. And the collaboration graph across a
workspace, which is where load, concentration and separation of duties are
visible in a way a table hides.

The graph has a fixed vocabulary of node and edge types, drawn once as a
diagram in the package documentation. That diagram is the ontology of a CHAP
workspace, and the node-link export follows it, so a graph tool or a knowledge
graph can take the export without a mapping step.

## Principles

**Small samples.** A new adopter has twelve decisions in month one, and
kappa, drift detection and calibration curves need far more. Every statistic
carries its uncertainty, intervals are Wilson intervals, posteriors are shown
rather than point estimates where the count is small, and a function that
lacks the data to answer says so and says how much more it needs.

**Every analysis names its intervention.** Override rate by tag revises a
prompt. Low inter-rater agreement on a policy means the policy is ambiguous.
Miscalibrated confidence retunes the routing thresholds. Abstention clustering
exposes a gap in reviewer coverage. Every chart that ships has its decision
attached.

**One specification, three renderings.** Charts are Vega-Lite specifications
built with Altair. A notebook renders them inline. The report inlines them in
one HTML file with the runtime embedded, so it opens from a file share with no
network. `vl-convert` turns the same specification into SVG or PNG for papers
and slides. Graph layouts are computed in Python and drawn with the same
renderer, as nodes and edges, so the report needs one runtime. The work that
matters is choosing the right questions and answering them carefully.

**Interactive where it changes the decision.** The report carries one filter
bar for workspace, date range, task kind, agent, reviewer, mode and tag, and
every chart and every headline number answers to it. Hovering shows the ids and
the rationale. Clicking a bar shows the rows behind it. Brushing the time axis
re-scopes the page. All of it runs in the browser from data embedded in the
file.

**Data scientists first.** The library returns DataFrames and chart
specifications. The report and the briefs are assembled from them, so anything
the report shows can be reproduced in a notebook from the same call.

**Redaction from day one.** Artefacts contain customer messages, contracts and
source code. The redaction hook in the loaders applies to the report and the
briefs as well. Artefact content stays out of a report unless the caller opts
in.

**Python.** The protocol packages hold TypeScript and Python at behavioural
parity because two implementations of a specification are what make it a
specification. This layer reads the wire format and its audience works in
pandas, so it is Python. A TypeScript consumer reads its tables through a
Parquet or CSV export, from `Frames.to_csv` or pandas' own writers, and its
charts as Vega-Lite JSON.

## Stages

Each stage ships something usable on its own.

### Stage 1: the dataframe layer

[`chap-analytics`](https://github.com/BrightbeamAI/chap/tree/main/packages/chap-analytics),
the projection from a chain to tables, loadable from a live coordinator, a
SQLite file, a JSON export or an HTTP endpoint, with every column declared
with its dtype and provenance.

It also does the work that otherwise gets done in every notebook. It parses
`confidence` from its decimal-string wire form. It separates review passes, so
a task sent back for revision is measured from each pass's own opening. It
reconstructs the corrected artefact from the patch, counts the tasks the
server mints on escalation and supersession, and marks the rows whose id had
to be inferred from the order of events.

Complete. Two columns are added for the assurance row of the table above:
`events.signed` (the envelope carried a signature) and `events.receipt` (a
SCITT receipt exists for the entry).

### Stage 2: the decision layer, one question at a time

`chap_analytics.stats` returns tidy frames, one row per group, with the
estimate, its interval, its sample size and a `sufficient` flag.
`chap_analytics.charts` turns each into a Vega-Lite specification.
`chap_analytics.briefs` turns each into a short text with the numbers filled
in and the decision it informs stated.

Delivered as slices, one row of the decision table per slice, each slice
complete on its own: the statistic, its chart, its brief, its tests, and a
worked example on the sample week. In order:

1. Override, rejection and approval rates with intervals, and the refine and
   reverse split. The first number a team asks for.
2. Patch path frequency by task kind, with drill-through to rationales.
3. Calibration: reliability diagram, expected calibration error, Brier score.
4. Time to decision as a survival curve with censoring, latency by reviewer
   and kind, and the open queue.
5. Promotion readiness: the Beta posterior against a threshold, and the
   sequential test.
6. Reviewer agreement: Fleiss' kappa with a stated minimum, and abstention
   clustering.
7. Whisper and handoff analytics.
8. Chain assurance: chained, signed and receipted fractions over time.
9. The graph: `chap_analytics.graph` builds the node-link graph from a
   `Frames` object, with the lineage of one outcome and the collaboration
   graph of a workspace as the two views, and the four graph rows of the
   decision table as its statistics. Exports as node-link JSON and GraphML.
10. Drift: the CUSUM, with its false-alarm rate as a parameter.

Statistics use numpy and pandas only. Altair and `vl-convert` are an optional
extra, `chap-analytics[viz]`, and networkx is another, `chap-analytics[graph]`,
so the tables and statistics install without either.

### Stage 3: the report

`chap_analytics.report` assembles the slices into one standalone HTML file.

The front page states the headline for each decision in the table: the number,
its interval, the sample size, and one line on what it means for that
workspace. Below it, one section per decision with the chart and the brief.
The filter bar at the top applies to everything on the page. The collaboration
graph has its own section, and any task, decision or artefact on the page
opens its lineage view.

The report is built from the same `Frames` object a notebook uses, so it is
reproducible from the chain, and it is regenerated rather than edited. Data is
embedded per chart, pre-aggregated, so the file stays small and raw artefacts
stay out of it. A report for a month of a busy workspace is a single file that
opens from disk.

Done when the sample week renders as a report with every row of the decision
table present, a test confirms every resource in the file is inlined, and the
filter bar re-scopes every chart and every headline.

### Stage 4: watching a live workspace

`chap_analytics.watch` reads `audit.read` incrementally from a sequence
cursor, re-projects, and regenerates the report or a brief on a schedule. The
CUSUM in the decision table runs here with a declared false-alarm rate, so a
prompt or model change can be judged within days rather than at the end of a
quarter, and an alarm names the metric, the window and the tasks that moved
it. A callback hook lets a deployment route an alarm to wherever it routes
alerts.

### Stage 5: exports

- Evaluation cases in the shape harnesses expect: input, agent output,
  human-corrected output, rationale
- Prompt-revision candidates: the override clusters most worth addressing,
  ranked by frequency and by refine or reverse
- Routing-policy calibration: thresholds fitted to observed outcomes, with the
  reliability diagram that justifies them

### Stage 6: models

Reviewer severity against agent quality, as a mixed-effects or item-response
model that separates how strict a reviewer is from how good the work was. This
needs more data than most workspaces hold in their first months, so it ships
as `chap-analytics[models]` and declines to fit below its minimum.

## Testing the layer

Statistics are checked against synthetic chains with known truth: a generated
week with a fixed override rate has to produce an interval that covers it, a
generated set of confidence values with a known miscalibration has to produce
the matching reliability curve, and a censored latency set has to reproduce
its survival function. Graph statistics are checked the same way: a generated
workspace with one reviewer deciding everything for one agent has to report
that concentration, and a generated path with no human decision on it has to
be listed as uncovered. Every chart specification is validated against the
Vega-Lite schema. The report is checked to be self-contained, every resource
inlined. The notebook is executed in the suite, as it is today.

## Protocol gaps the analytics expose

Building the projection is the cheapest way to discover what CHAP records
lightly. Each of these becomes a specification proposal once the analytics
show it matters.

**Task difficulty.** Separating a hard task from a weak agent rests on the
model in stage 6. A `difficulty` routing hint would make it a direct
measurement.

**Reviewer effort.** A decision's `ts` minus the review's `requested_at` is
elapsed time. A reviewer who answers after three days may have spent ninety
seconds. Claims about reviewer diligence need something that distinguishes the
two.

**What the reviewer saw.** Every analysis attributes a decision to an artefact,
and that attribution assumes the decider saw what the chain says they saw. The
digest binds a decision to content rather than to a rendering of it.

**Server-minted identifiers.** A task, whisper, deliberation or handoff id is
returned in the result, and the audit log records envelopes. Anything acted on
later is recoverable, because the acting envelope names it, and the protocol's
own constraints settle more: a lapse concerns a whisper whose deadline had
passed, a vote comes from an invited participant, an acceptance from the named
recipient. Read from envelopes alone, a share of task, whisper, handoff and
deliberation rows can only be identified by the order of events, and those
rows are marked. Recording the minted id in the audit entry beside the
envelope would make identification exact for all of them.
