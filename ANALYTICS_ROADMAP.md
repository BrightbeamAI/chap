# CHAP analytics roadmap

CHAP records what people decided about agent work. This is the plan for the
layer that reads those records back and says something useful about them.

## The data

Every override on a CHAP chain carries the RFC 6902 patch that changed the
draft and a rationale in the reviewer's words. It may carry policy references,
workspace-defined tags, and a flag saying whether the reviewer refined the
agent's decision or reversed it. The chain around it carries the artefact it
started from. Every decision carries a reviewer, a kind, a rule and a
timestamp. Every task carries its assignee, its mode and its state history,
and the routing hints it was given.

That is a human-labelled evaluation set with provenance and counterfactuals,
produced as a side effect of ordinary review. The layer treats it as one.

> A CHAP chain is a supervision dataset. This layer is what lets it be read as one.

Visualisation is how the dataset is inspected. Exports into evaluation
harnesses, prompt-revision candidates and routing-policy calibration are what
it is for.

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

## Principles

**Honest at small n.** A new adopter has twelve decisions in month one, and
kappa, drift detection and calibration curves need far more. Every statistic
carries its uncertainty, intervals are Wilson intervals, and a function that
lacks the data to answer says so.

**Existing renderers.** The layer emits specifications for Vega-Lite or
Observable Plot. The differentiated work is the semantic layer: the vocabulary
of well-posed questions and the care taken in answering them.

**Every analysis names its intervention.** Override rate by tag revises a
prompt. Low inter-rater agreement on a policy means the policy is ambiguous.
Miscalibrated confidence retunes the routing thresholds. Abstention clustering
exposes a gap in reviewer coverage.

**Data scientists first.** The library returns DataFrames. Dashboards and
executive summaries are built on top of it.

**Redaction from day one.** Artefacts contain customer messages, contracts and
source code. A redaction hook is in the first release.

**Python.** The protocol packages hold TypeScript and Python at behavioural
parity because two implementations of a specification are what make it a
specification. This layer reads the wire format and its audience works in
pandas, so it is Python. A TypeScript consumer reads its tables through a
Parquet or CSV export, from `Frames.to_csv` or pandas' own writers.

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

Done when a chain projects to tables and every table is documented, and when
random workspaces driven against a live coordinator agree with what that
coordinator holds on every row the projection vouches for, from either source.
Complete.

### Stage 2: descriptive statistics with intervals

Rates, distributions and counts, each with a confidence interval and a sample
size, declining below a stated minimum.

- Override, rejection, approval and abstention rates by agent, reviewer, task
  kind, policy and tag, with Wilson intervals
- Time to decision as a distribution, with open reviews censored
- Tag and policy-reference co-occurrence
- Refining and reversing, from `intent_preserved`

### Stage 3: the analyses that are hard to get elsewhere

- **Calibration.** `routing_hints.confidence` against realised outcome:
  reliability diagrams, Brier score, expected calibration error. "Your agent
  says 0.9 and is overridden four times in ten" is the most actionable sentence
  this data can produce.
- **Reviewer severity against agent quality.** A mixed-effects or item-response
  model separating how strict a reviewer is from how good the work was.
- **Inter-rater reliability.** Fleiss' kappa across `quorum:N` and
  `all_approve` reviews, where several decisions exist on one artefact. Low
  agreement on a policy points at the policy.
- **Drift.** Sequential change detection on override rate, with a defensible
  alarm threshold, so a prompt change can be judged within days.

### Stage 4: exports

- Evaluation cases in the shape harnesses expect: input, agent output,
  human-corrected output, rationale
- Prompt-revision candidates: the override clusters most worth addressing,
  ranked by frequency and severity
- Routing-policy calibration: thresholds fitted to observed outcomes

### Stage 5: visualisation

Thin adapters emitting Vega-Lite specifications, and a gallery of worked
examples on generated chains. Notebook first.

## Protocol gaps the analytics expose

Building the projection is the cheapest way to discover what CHAP records
lightly. Each of these becomes a specification proposal once the analytics
show it matters.

**Task difficulty.** Separating a hard task from a weak agent rests on the
mixed model in stage 3. A `difficulty` routing hint would make it a direct
measurement.

**Reviewer effort.** A decision's `ts` minus the review's `requested_at` is
elapsed time. A reviewer who answers after three days may have spent ninety
seconds. Claims about reviewer diligence need something that distinguishes the
two.

**What the reviewer saw.** Every analysis in stage 3 attributes a decision to
an artefact, and that attribution assumes the decider saw what the chain says
they saw. The digest binds a decision to content rather than to a rendering of
it.

**Server-minted identifiers.** A task, whisper, deliberation or handoff id is
returned in the result, and the audit log records envelopes. Anything acted on
later is recoverable, because the acting envelope names it, and the protocol's
own constraints settle more: a lapse concerns a whisper whose deadline had
passed, a vote comes from an invited participant, an acceptance from the named
recipient. Across random workspaces read from envelopes alone, roughly half
the task rows and six to eight in ten of the whisper, handoff and deliberation
rows are identified beyond doubt, and the rest are marked. Recording the
minted id in the audit entry beside the envelope would make it all of them.
