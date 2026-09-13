# CHAP analytics: a development roadmap

CHAP records what humans decided about agent work. This is the plan for the
layer that reads those records back and says something useful about them.

The claim the protocol already makes is that "the overrides your reviewers were
already making accumulate into supervision data you'd otherwise have to
commission." Today the whole of that claim is discharged by 385 lines across
`reference/core-plus-review/analyze-overrides.ts` and
`reference/python/analyze_overrides.py`: tag counts, policy-reference counts,
an `intent_preserved` split, and a bar chart drawn in hyphens. The protocol is
released and stable. The layer that makes anyone care about it is a stub.

---

## 1. What the data actually is

Worth stating plainly, because it decides everything downstream.

Every override on a CHAP chain carries the artefact it started from, the RFC
6902 patch that changed it, the result, a free-text rationale, the policy
references invoked, workspace-defined tags, and a flag saying whether the human
was refining the agent's decision or reversing it. Every decision carries a
reviewer, a kind, and a timestamp. Every task carries its assignee, its mode,
its routing hints and its full state history.

That is a continuously generated, human-labelled evaluation set with provenance
and counterfactuals. Teams commission worse versions of this from contractors.

So the north star is not a charting library. It is:

> **A CHAP chain is a supervision dataset. This layer is what turns it into
> one.**

Visualisation is how the dataset is inspected. Exports into evaluation
harnesses, prompt-revision candidates and routing-policy calibration are what
it is for.

### What each source can and cannot give you

The two ways to read a chain are not equivalent, and the library must say so
rather than quietly returning nulls.

| Source | Carries | Missing |
|---|---|---|
| `audit.read` over MCP or HTTP | Every envelope: all request parameters, in order, hash-linked | What only the server computed: deliberation outcomes, route-decision outcomes, and which server-minted id a creation produced |
| A `SqliteStore` file or workspace snapshot | Full state: tasks, overrides, deliberations, handoffs, route decisions, and the audit log | Nothing, but requires filesystem access to the deployment |

The envelope stream is nonetheless self-sufficient for the analyses that
matter. `review.request` carries the artefact under review and
`decide.override` carries the patch, so the before and after can be
reconstructed by **replaying** the chain rather than by reading server state.
That property is what lets the whole layer work against a plain `audit.read`,
which is the only thing available to an MCP client, and it is checkable:
replayed artefacts must equal the snapshot wherever both exist, and stage 1
checks it on every override in its differential suite.

---

## 2. Principles

These are constraints, not aspirations. Each one rules something out.

**Small n is the default, and pretending otherwise is disqualifying.** A new
adopter has twelve decisions in month one. Kappa, drift detection and
calibration curves are meaningless there. For a protocol whose entire claim is
*honest* records, a library that prints a confident number on n=12 does more
damage than one that prints nothing. Every statistic carries its uncertainty,
intervals are Wilson rather than normal-approximation, and functions refuse
below a stated minimum rather than returning a number nobody should use.
"Not enough data yet" is a supported answer.

**Do not build rendering.** Charting is solved. A grammar-of-graphics
adapter over Vega-Lite or Observable Plot is a week; a bespoke rendering layer
is a year and will be worse. The differentiated asset is the semantic layer,
the vocabulary of well-posed questions, and the statistical care taken
answering them.

**Every analysis names its intervention.** Override rate by tag revises a
prompt. Low inter-rater agreement on a policy means the policy is ambiguous.
Miscalibrated confidence retunes the routing thresholds. Abstention clustering
exposes a gap in reviewer coverage. An analysis that names no action gets cut,
however good the chart looks.

**Serve the data scientist first.** Operators want a dashboard, executives want
three numbers, data scientists want a DataFrame and to be left alone. The
library serves the third directly; the other two are built on top of it as
demonstrations, not baked into it.

**Redaction is a day-one concern, not a later feature.** Artefacts contain
customer messages, contracts and source code. `HANDBOOK.md` §8.3 already
commits CHAP to a right-to-be-forgotten position. A field allowlist and a
redaction hook exist before anyone points this at production data.

---

## 3. A deliberate departure from the parity rule

Every other part of CHAP holds TypeScript and Python at behavioural parity, and
that rule is load-bearing: two independent implementations of a specification
are what make it a specification rather than a product.

This layer is **Python only**, and that is a considered exception. It is not
protocol. It emits no envelopes and defines no wire format, so there is nothing
for a second implementation to disagree about. The audience is data scientists,
who work in pandas. Building a TypeScript twin would double the cost to reach
an audience that does not want it.

If a TypeScript consumer needs these tables later, the honest route is a JSON
or Arrow export from this package, not a reimplementation.

---

## 4. Stages

Each stage ships something usable on its own. Nothing here depends on a stage
after it.

### Stage 1: the dataframe layer

**`chap-analytics`, the projection from a chain to tables.**

One documented, versioned, tabular representation of a CHAP chain, loadable
from a live coordinator, a SQLite file, a JSON export, or an HTTP endpoint.
Eleven tables: `events`, `tasks`, `decisions`, `overrides`, `patch_ops`,
`participants`, `deliberations`, `votes`, `whispers`, `handoffs`, `routing`.

This is the whole foundation. Everything later is a function of these tables,
and a data scientist who dislikes every opinion in stage 2 can stop here and
use pandas.

It also does the tedious work that otherwise gets done wrong in every notebook:
parsing `confidence` from its decimal-string wire form into a float, measuring
decision latency from the opening of the review pass the decision belongs to,
flattening RFC 6902 patch operations into countable rows, and marking which
columns the current source could not populate.

Two of those turned out to be harder than they look, and both are the reason
the layer exists rather than a notebook. A task can be reviewed more than once,
and the passes must not be pooled: the coordinator replaces a review outright
when one is requested on a task that is not currently under review, so an
approval from the first pass says nothing about the second, and a rate computed
across both counts approvals of artefacts that no longer exist. And a
server-minted identifier is returned in the *result* while the log records
envelopes, so pairing a creation to its id is sometimes forced by the ordering
and sometimes a guess; the tables carry `id_certain` and say which.

**Done when** a chain projects to tables, every table is documented with dtypes
and provenance, a test proves no projection loses or invents a row, and random
workspaces driven against a live coordinator agree with what that coordinator
holds, on every row the projection vouches for, from either source.

### Stage 2: descriptive statistics with honest intervals

Rates, distributions and counts, each returned with a confidence interval and a
sample size, refusing below a threshold.

- Override, rejection, approval and abstention rates by agent, reviewer, task
  kind, policy and tag, with Wilson intervals
- Time to decision, as a distribution rather than a mean, with reviews still
  open correctly censored rather than dropped
- Tag and policy-reference co-occurrence
- Refining versus reversing splits from `intent_preserved`

**Unlocks** the first honest version of the override learning report the README
already promises.

### Stage 3: the analyses that are hard to get elsewhere

This is where the layer stops being a reporting tool.

- **Calibration.** `routing_hints.confidence` against realised outcome:
  reliability diagrams, Brier score, expected calibration error. "Your agent
  says 0.9 and is overridden four times in ten" is the single most actionable
  sentence this data can produce.
- **Reviewer severity against agent quality.** A mixed-effects or
  item-response model separating how strict a reviewer is from how good the
  work was. Every team argues about this and none can currently settle it.
  Intellectually the strongest piece here, and the hardest to obtain any other
  way.
- **Inter-rater reliability.** Fleiss' kappa across `quorum:N` and
  `all_approve` reviews, where several decisions exist on one artefact. Low
  agreement on a policy indicts the policy, not the agent.
- **Drift.** Sequential change detection, CUSUM rather than a rolling average,
  on override rate. Answers whether last Tuesday's prompt change made things
  worse, with a defensible alarm threshold.

**Unlocks** the claim that CHAP improves the system rather than merely
recording it.

### Stage 4: exports

The supervision dataset leaving the building in a form other tools accept.

- Evaluation cases in the shape eval harnesses expect: input, agent output,
  human-corrected output, rationale
- Prompt-revision candidates: the override clusters most worth addressing,
  ranked by frequency and severity
- Routing-policy calibration: thresholds fitted to observed outcomes rather
  than guessed

**Unlocks** the loop closing. Data goes back into the system it came from.

### Stage 5: visualisation

Thin adapters emitting Vega-Lite specifications, plus a gallery of worked
examples on synthetic chains. A notebook-first experience, with the dashboard
built on the library as a demonstration rather than shipped inside it.

**Unlocks** the illustrative half, which is what makes the value legible to
someone who will not read a table.

---

## 5. Protocol gaps this will surface

Building the projection is also the cheapest way to discover what CHAP does not
record. Three are already visible from the type surface, and none should be
fixed speculatively before the analytics prove they matter.

**No task difficulty signal.** Separating "hard task" from "weak agent" rests
entirely on the mixed model in stage 3. A `difficulty` routing hint would make
it a direct measurement instead of an inference.

**No reviewer effort.** A decision's `ts` minus the review's `requested_at` is
elapsed time, not attention. A reviewer who answers after three days may have
spent ninety seconds. Any claim about reviewer diligence is unsupported until
something distinguishes the two.

**No record of what the reviewer saw.** This is the bidi and WYSIWYS issue
already drafted and still unfiled. It matters more here than anywhere: every
analysis in stage 3 attributes a decision to an artefact, and that attribution
assumes the decider saw what the chain says they saw. The digest binds a
decision to content, not to a rendering of it.

**No server-minted identifier in the log.** A task, whisper, deliberation and
handoff id all come back in the *result*, and the audit log records envelopes.
Anything acted on later is recoverable, because the acting envelope names it,
but where two are created before either is touched the ordering alone cannot
say which is which. Stage 1 measures the ambiguity and reports it per row
rather than hiding it, and the protocol constraints narrow it further: a lapse
concerns a whisper whose deadline had passed, a vote comes from someone the
deliberation invited, an answer from someone the whisper was addressed to, an
acceptance from the named recipient, and an id the caller supplied is in the
envelope itself. Across random workspaces read from envelopes alone, roughly
half the task rows and between six and eight in ten of the whisper,
deliberation and handoff rows can be identified beyond doubt; the differential
suite holds a floor under those figures. Recording the minted id in the audit
entry alongside the envelope would make it all of them. Worth doing only if
the analytics prove the ambiguity costs something real.

---

## 6. What this is not

Named so the scope does not drift.

It is **not a dashboard product**. It is a library; a dashboard is a
demonstration built on it.

It is **not a rendering engine**. It emits specifications for renderers that
already exist.

It is **not real-time monitoring**. Alerting on a live chain is a different
system with different latency requirements, and conflating the two would
compromise both.

It is **not part of the protocol**. Nothing here changes the wire format,
constrains an implementation, or belongs in `SPECIFICATION.md`. A coordinator
that never runs any of this is fully conformant.
