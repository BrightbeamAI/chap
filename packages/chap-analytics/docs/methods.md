# Methods

Every statistic in `chap_analytics.stats`, `chap_analytics.graph` and
`chap_analytics.models`: what it computes, what it assumes, the minimum it
asks for, and how to read it. A rate comes back as a tidy DataFrame with
one row per group: the estimate, its Wilson interval and the count behind
it. Counts without an interval (`outcomes`, `tags`, `patch_paths`,
`abstentions`) and one-row-per-item tables (`latency`, `sequential`,
`cusum`) are described as such where they occur. Where an estimate has a
minimum, the frame carries a `sufficient` flag and its `attrs["minimum"]`
records the count the flag was judged against; `calibration` keeps both in
`attrs`. Arguments after the `*` in a signature are keyword-only. A function
that lacks the data to say anything returns its columns and no rows.

The population for every rate is the *decided* task: one whose last review
pass ended in a reviewer's approve, override, reject or abstain. Tasks that
were escalated, cancelled, superseded, completed without a decision or are
still open sit outside the denominator; `outcomes()` reports them so the
denominator is visible. A task sent back for revision and reviewed again is
one task with two review passes, and its outcome reads from the decision
that settled the last pass.

## Rates

**`rates(f, by=None, *, minimum=10)`.** Approval, override, rejection and
abstention shares among decided tasks, each with a 95% Wilson score
interval. The Wilson interval is used throughout because it behaves at
small counts and at rates near 0 and 1, where the normal approximation
gives intervals that cross the boundaries. `by` groups on a column of
`tasks` (`kind`, `assignee`, `mode`, `criticality`, `risk_tier`) or on
`reviewer`, the reviewer who settled the task. Tags label corrections rather
than tasks, so `tags()` reports them separately with the share of overrides
carrying each tag.

**`rates_over_time(f, freq="W", by=None, *, minimum=10, when="settled_at")`.** The same
per calendar period, bucketed on the time the task settled. `freq` is a
pandas offset alias: `D`, `W`, `MS`. Weekly buckets with fewer than ten
decided tasks are flagged insufficient and drawn anyway; the interval band
shows how little they pin down.

**`refine_reverse(f, by=None, *, minimum=10)`.** Among overrides, the share
whose `intent_preserved` flag says the reviewer kept the agent's decision
and improved its expression, against the share that reversed it. The share
is computed over overrides where the flag was stated; `unstated` counts the
rest. The two call for different fixes: a high reversing share points at
the policy or the model, a high refining share at wording and format.

## Where corrections land

**`patch_paths(f, by="task_kind", *, top=None, depth="top_path")`.** For each
group, how many overrides touched each part of the artefact, from the RFC
6902 operations on the override. An override touching a path twice counts
once. `share` is the fraction of the group's overrides that touched the
path, so it reads as "N in ten corrections of this kind edit the reply".
`depth="path"` uses the full JSON Pointer.

**`path_rationales(f, path=None, kind=None)`.** The overrides behind one
cell of the heatmap: who corrected what, and the rationale they gave. This
is what the report shows on a click.

## Calibration

**`calibration(f, bins=10, *, minimum=30)`.** The reliability table: reported
confidence, parsed from `routing_hints.confidence` or the confidence on
`task.complete`, against the share of tasks reviewers accepted as drafted.
One row per confidence bin with the count, the mean reported confidence,
the observed acceptance and its Wilson interval. Abstentions are set aside
because they are neither acceptance nor correction. `attrs` carry `n`,
`ece`, `brier`, `sufficient` and `minimum`; `calibration_summary` returns
the same as a dict.

Expected calibration error is the count-weighted mean absolute gap between
each bin's mean confidence and its observed acceptance. The Brier score is
the mean squared difference between the reported confidence and the
accept-or-not outcome, so it rewards both calibration and resolution. An
agent whose reported confidence carries no information has a flat
reliability curve at the overall acceptance rate and an ECE equal to the
gap between its typical confidence and that rate. Thirty decided tasks with
a confidence is the minimum for the flag; the curve needs more than that
before individual bins mean much, which is why the chart drops bins with
fewer than five tasks.

## Time to decision

**`latency(f)`.** One row per review pass on a task that was decided or
is still open: when the pass opened, how long until its first decision,
and whether it is still open. An open pass is measured to the end of the
chain and marked `event=False`, so it can be censored rather than dropped.
Passes on tasks that were escalated, cancelled or superseded before a
decision are outside this table.

**`survival(f, by=None)`.** The Kaplan-Meier estimate of the share of
reviews still waiting after each duration, with open reviews censored.
Dropping the open reviews flatters every latency figure, because the
reviews that take longest are the ones most likely to be open when the
chain is read. `low` and `high` are Greenwood 95% bounds. The curve's
median is the time by which half of reviews have been decided, counting the
ones still waiting.

**`latency_by(f, by="reviewer", *, minimum=5)`.** Count, median, 90th
percentile and mean time to first decision per group, on decided passes,
with the number of passes still open beside them. An open pass has no
reviewer yet, so with `by="reviewer"` the open passes sit on a row of their
own with no name; group by `task_kind` or `assignee` to see where they
wait. Read the median and the 90th percentile together: a reviewer with a
short median and a long tail is a queue with occasional stalls, which is a
different problem from a slow reviewer.

**`open_queue(f)`.** Reviews still waiting, oldest first, with their age at
the end of the chain.

## Promotion readiness

**`promotion(f, threshold=0.10, by="assignee", *, mode=None, prior=(1, 1), credible=0.90, minimum=20, counting="reversing")`.**
Whether an agent's rate of substantive correction is under a threshold.
`mode` restricts the tasks to one mode, so a trial agent is judged on its
trial work. `counting="reversing"` counts overrides with `intent_preserved` False plus
rejections; `"changed"` counts every override and rejection. With a
Beta(a, b) prior, the posterior over the true rate after k corrections in n
decided tasks is Beta(a + k, b + n - k). The table gives its mean, a
credible interval and `p_below_threshold`, the posterior probability that
the true rate is under the bar.

That probability is meaningful at any n, which is the point of using a
posterior for this question: twelve decisions give a wide posterior and a
number near the prior, and the reader sees both. `sufficient` marks groups
with at least twenty decided tasks so the reader knows how much of the
number is prior. The uniform prior is deliberate; a workspace with a
history can pass the counts from a previous quarter as the prior.

**`sequential(f, p0, p1, *, alpha=0.05, beta=0.20, by="assignee", counting="reversing")`.**
Wald's sequential probability ratio test on the same rate, task by task in
time order. `p0` is the rate an agent may run at and `p1` the rate that
should stop a promotion; `alpha` is the chance of holding a good agent and
`beta` the chance of promoting a bad one. Each row carries the cumulative
log-likelihood ratio and a verdict: `promote` once the ratio crosses the
lower bound, `hold` once it crosses the upper, `continue` between. The test
stops at its first crossing, which is what makes it sequential: what came
after the crossing did not count, and the chart draws the line to that
point and labels it. The verdict in the last row is the current state of
the test. The test answers a two-point question, `p0` against `p1`, so an
agent whose true rate sits between them can be promoted by it while the
posterior above still shows a fair chance of being over the bar. Read the
two together.

## Reviewer agreement

**`agreement(f, *, minimum=10)`.** Fleiss' kappa over review passes where two
or more reviewers decided the same artefact, on accept against change
(override or reject). Fleiss' statistic assumes the same number of raters on
every subject, so passes are grouped by how many reviewers decided them and
one row is returned per group size.

Read it with the protocol in mind. Under `quorum:N` and `all_approve`, the
review rules that ask more than one reviewer to approve, a pass stays open
for a second decision only while the earlier reviewers approve; an override
or a rejection settles it. So the passes compared here
are the ones a first reviewer accepted, and kappa measures how often the
next reviewer agreed with an acceptance. When almost every shared pass is
an acceptance, observed agreement is high and kappa is low, because kappa
discounts the agreement expected from the base rate and there is little
disagreement left to measure. The brief says so in that case. For
independent judgements on one question, `vote_agreement()` computes the same
statistic over deliberations, where votes are cast without seeing each
other.

**`pairwise_agreement(f, *, minimum=10)`.** Cohen's kappa for each pair of
reviewers over the passes both decided. One row per pair; the agreement
matrix in the report is drawn from it.

**`abstentions(f, by="task_kind")`.** Abstentions by their stated category
and group, with each group's decided count for scale. A cluster of
abstentions on one kind of task is a gap in reviewer coverage.

## Questions and handoffs

**`whispers(f, by="asker", *, minimum=5)`.** How often agents ask, how often
the question lapses, and how fast an answer comes. The lapse rate carries a
Wilson interval over whispers that reached a resolution (answered or
lapsed); pending ones are counted apart. A lapse means the question's
default answer stood and the agent went on with it, so a high lapse rate is
a finding about the task inputs or the routing of questions rather than
about the agent. The brief treats a lapse rate above a quarter as high.

**`handoffs(f, by="recipient", *, minimum=5)`.** Acceptance rate per recipient
(or per proposer with `by="proposer"`), with a Wilson interval over resolved
handoffs, and the median time to a resolution.

## Chain assurance

**`assurance(f, freq="D")`.** Per period, how many entries the chain holds
and the share that are hash-linked (`prev_hash` present), signed (the
envelope carried a `sig`), and covered by a recorded `audit.submit_to_scitt`
range. SCITT is the IETF's transparency-log architecture (Supply Chain
Integrity, Transparency and Trust). The three columns record what the
chain carries; they do not verify a hash, check a signature or fetch a
receipt. A hash-linked entry can be checked against the one before it; a
signed entry can be checked against its sender's key; a submitted entry
was sent to a log, and the receipt lives outside the chain.

## Drift

**`cusum(f, *, target=None, shift=0.10, false_alarm_runs=1000, baseline=100, by=None, seed=0)`.**
A Bernoulli CUSUM on whether each decided task was changed, in time order.
`target` is the rate the process is believed to run at; when unset it is
the rate over the first `baseline` decided tasks. `shift` is the rise the
chart is tuned to catch, so the alternative is `target + shift`. The
decision interval is chosen by simulation so that, when the rate has not
moved, the average run between false alarms is near `false_alarm_runs`
tasks: the smallest interval on a 0.25 grid whose simulated average run
reaches the target.
One row per decided task with the statistic, the interval and `alarm`
where it was crossed; the statistic resets after an alarm.

Two things to know when reading it. The chart is tuned to one size of
shift: a smaller rise is caught later, a larger one sooner. And the
baseline is the first hundred tasks, so a workspace whose first hundred
tasks were unusual should pass `target` explicitly.

## The graph

The graph functions live in `chap_analytics.graph` and are described in
[`graph.md`](graph.md). Their statistics:

**`centrality(f)`.** In and out degree (distinct counterparts), weighted in
and out (work items) and betweenness on the collaboration graph, per
participant. Betweenness is Brandes' algorithm over shortest paths on the
directed graph, counting hops rather than weights; it is high for whoever
sits between others' work.

**`concentration(f, *, minimum=10)`.** For each assignee, the top reviewer's
share of its decisions and the Herfindahl index over reviewers. An index of
1.0 means one reviewer decides everything; with three reviewers sharing
evenly it is 0.33.

**`coverage(f)`.** For each task whose work went out (approved, overridden,
completed after rejection, completed bypassing review, completed without
review), whether a person was on its path: a human decided on the task or
on a task it superseded, or a human did the work. `attrs` carry `shipped`,
`covered`, `share` and the uncovered task ids.

**`duties(f)`.** Same-actor pairs across roles that should be separate:
`self_review`, `delegator_review`, `self_handoff` and `agent_decided`.

## Models

**`models.reviewer_severity(f, *, minimum_decisions=30, minimum_reviewers=2, minimum_per_unit=5, l2=1.0, iterations=2000, learning_rate=0.05)`.**
A reviewer who changes half of what they see may be strict, or may be
seeing weak work. With several reviewers deciding on several agents the two
can be separated by fitting one parameter per agent (quality) and one per
reviewer (severity) so that

    P(accepted as drafted) = sigmoid(quality[agent] - severity[reviewer] + baseline)

which is a Rasch-style logistic model. The fit is penalised maximum
likelihood in numpy; the L2 penalty keeps a reviewer or agent with few
decisions near zero rather than at infinity, and the `thin` column marks
the units that rest on fewer than `minimum_per_unit` decisions. Each
estimate carries an approximate standard error from the diagonal of the
penalised observed information, which leaves out the correlation between a
reviewer's and an agent's estimates. Both groups are identified relative to
their own mean, so a severity of +1.0 is one log-odds unit stricter than
the average reviewer on this chain.

Below thirty decisions, or with one reviewer, the function declines to fit
and `attrs["reason"]` says why. The model separates severity from quality
only where reviewers overlap on agents; a workspace where each reviewer
sees one agent gives estimates that are formally identified by the penalty
alone, and the `thin` flags and standard errors will say so.

## Conventions

Intervals are 95% unless named otherwise; the promotion posterior uses a
90% credible interval by default. Durations are in seconds in the tables;
the survival and latency charts show hours, and the other charts say the
unit in their tooltips. Every function is deterministic for its inputs; the
CUSUM's simulated decision interval takes a seed.
