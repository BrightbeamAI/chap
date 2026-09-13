# chap-analytics

[![PyPI](https://img.shields.io/pypi/v/chap-analytics?style=flat-square&logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/chap-analytics/)
[![Python](https://img.shields.io/pypi/pyversions/chap-analytics?style=flat-square)](https://pypi.org/project/chap-analytics/)
[![Licence](https://img.shields.io/badge/licence-Apache_2.0-7c3aed?style=flat-square)](https://github.com/BrightbeamAI/chap/blob/main/LICENSE)

A CHAP audit chain, as pandas tables.

A CHAP chain records what people decided about agent work: what an agent
produced, what a person changed, why, under which rule, and when. That is a
human-labelled evaluation set with provenance, generated as a side effect of
ordinary review. This package projects the chain into eleven documented tables
so it can be analysed as one.

It is stage one of the
[analytics roadmap](https://github.com/BrightbeamAI/chap/blob/main/ANALYTICS_ROADMAP.md)
and it stops at the tables. Statistics belong in a layer above, where their
assumptions can be stated.

## Install

```bash
pip install chap-analytics
```

Python 3.10 or later. `pandas` is the only dependency. Reading a chain from a
live `Coordinator` in the same process, or generating the sample week, needs
`chap-coordinator` as well:

```bash
pip install 'chap-analytics[coordinator]'
```

Reading a SQLite file, a JSON export or an HTTP endpoint needs the base
install alone.

## Quick start

```python
from chap_analytics import from_sqlite, frames

chain = from_sqlite("./chap.db", workspace="wsp_support")
f = frames(chain)

print(f.summary())

# Which part of the output do reviewers keep correcting?
f.patch_ops.groupby("top_path").size().sort_values(ascending=False)

# Does the agent's confidence track its outcomes?
f.tasks.groupby("outcome")["confidence"].describe()

# Are reviewers refining the agent's decision or reversing it?
f.overrides.intent_preserved.value_counts(dropna=False)

# Everything, as files.
f.to_csv("./week")
```

## A worked week

`chap_analytics.sample.support_desk()` generates a week at a support desk
against a real coordinator. An agent drafts replies to customer tickets and
three people review them. They approve most, correct some and send a few back.
One declares a conflict of interest, work changes hands at a shift change, the
agent asks two questions and gets one answer, a policy exception goes to a
vote, and one ticket is escalated to legal. Every action is a CHAP envelope,
and the chain comes back ready to project.

```python
from chap_analytics import frames
from chap_analytics.sample import support_desk

f = frames(support_desk())
```

Two guided versions of the same week ship with the repository:

- [`examples/chap_analytics_walkthrough.ipynb`](https://github.com/BrightbeamAI/chap/blob/main/packages/chap-analytics/examples/chap_analytics_walkthrough.ipynb)
  follows the envelopes from `audit.read` to the tables, with each table's
  grain, columns, dtypes and provenance explained beside the frames.
- [`examples/support_desk.py`](https://github.com/BrightbeamAI/chap/blob/main/packages/chap-analytics/examples/support_desk.py)
  prints ten short analyses of the week, each a count or a median with a line
  on what it is for.

The week is generated on each run, so it reflects the coordinator that is
installed. The test suite runs both.

## The tables

| Table | Grain |
|---|---|
| `events` | one row per audit log entry |
| `tasks` | one row per task |
| `decisions` | one row per approve, reject, override or abstain |
| `overrides` | one row per correction, with its diff summarised |
| `patch_ops` | one row per RFC 6902 operation within an override |
| `participants` | one row per participant per workspace |
| `deliberations` | one row per group decision |
| `votes` | one row per vote |
| `whispers` | one row per deadline-bound question |
| `handoffs` | one row per proposed handoff |
| `routing` | one row per routing decision |

Every column is declared in
[`schema.py`](https://github.com/BrightbeamAI/chap/blob/main/packages/chap-analytics/chap_analytics/schema.py)
with its dtype and its provenance. `describe()` prints the whole contract:

```python
from chap_analytics import describe
print(describe())
```

A column the source lacked a value for is present and null. Code downstream
can reference any column and see missingness rather than a `KeyError`.

## Four sources

```python
from chap_analytics import from_sqlite, from_url, from_json, from_coordinator

from_sqlite("./chap.db", workspace="wsp_support")     # the full snapshot
from_url("http://localhost:8080/chap", "wsp_support") # envelopes, via audit.read
from_json("export.json")                              # an audit.read result, a bare entry list, or a snapshot
from_coordinator(coord, workspace="wsp_support")      # a live coordinator, in-process
```

A SQLite file or a live coordinator carries the workspace snapshot: every
task, override, deliberation and handoff as the coordinator holds it, and the
audit log beside them. `audit.read`, which is what an MCP client can obtain,
returns the audit log alone: every request parameter, in order, and
hash-linked where the coordinator chains its log.

The tables are **replayed** from the envelope stream, so most of what is worth
analysing is available from either source. The artefact under review arrives
on `review.request` and the patch on `decide.override`, so the before and the
after of every correction come from the envelopes themselves. Three things
come from the snapshot alone: deliberation and routing outcomes, which the
server computes; the assignee a `task.route` chose, which `assignee_certain`
flags; and the certainty of which server-minted id belongs to which creation,
which the section on identity below explains.

How far the two reads agree is measured. Random workspaces are driven against
a real coordinator and read both ways. For every task, decision, override,
whisper, deliberation and handoff, the row either matches what the coordinator
holds or is marked `id_certain` false.

## What the projection handles for you

**Parses `confidence`.** Fractional values travel the CHAP wire as decimal
strings, because canonicalisation admits integers alone
([SPECIFICATION §7](https://github.com/BrightbeamAI/chap/blob/main/SPECIFICATION.md)).
The tables carry a float.

**Censors open work.** `lifetime_s` is null while a task is unsettled, and
`settled` says which rows are censored. A mean over the finished work alone
flatters every latency figure; the flag lets you say so.

**Knows when a decision settled a review.** Under `all_approve` or `quorum:N`
an approval may leave the review open. `is_final` is computed with the rule the
coordinator applies. Under `all_approve` the coordinator waits on the reviewers
it can name, and a review addressed to a group alone has none to wait on, so
it treats that review as first-approve.

**Separates the review passes.** A task sent back for revision is reviewed
again, and the coordinator starts that review afresh. `review_index` says which
pass a decision belongs to, `n_reviews` counts the passes, `latency_s` is
measured from each pass's own opening, and `outcome` is read from the decision
that settled the last pass.

**Reconstructs the correction.** `based_on` is the artefact the reviewer saw
and `result` is the patch applied to it, using the coordinator's own RFC 6902
semantics. Both are available from `audit.read` alone. Where the snapshot
carries the artefact the coordinator stored, that one is used, and the
differential suite requires the replayed one to equal it.

**Counts the tasks the server minted.** `escalate.raise` and
`control.supersede` create a successor without a `task.create` envelope, and
give it what the original had. An escalation successor takes the original's
mode, and its kind where the spec omits one. A supersession successor takes
the original's assignee and mode where the spec omits them, and gets the same
`review_required` default a `task.create` would. The successor is a row like
any other, with `supersedes` linking it back.

**Keeps the orphans.** A task created and left alone still gets a row.

**Says how sure it is.** `id_certain` and `assignee_certain` mark the rows where
the chain admits more than one reading, so the population to draw conclusions
from is a filter away.

## Redaction

Artefacts hold whatever the agent was working on: customer messages,
contracts, source code. A redactor sees every one of them before anything
reaches a table.

```python
from chap_analytics import from_sqlite, redact_artefacts, frames

f = frames(from_sqlite("./chap.db", redact=redact_artefacts))
```

What goes: task inputs and outputs, the artefact under review and the
corrected one, the values a patch writes, free-text whisper answers under
either of their two names, lapse defaults, the inputs of a successor an
escalation or supersession mints, and the copies a snapshot holds. All of it
goes in both the envelope stream and the workspace snapshot that also contains
it. A test plants a marker in each of those places and searches every cell of
every table for all of them.

What stays, because it is the analysis: counts, rates, latencies, tags, policy
references, patch paths and operations, which option a whisper answer chose,
and the words participants wrote about the work. A reviewer's rationale,
comment and decline reason, a handoff summary and a deliberation question are
the people's account of what they did, and the point of the chain. A whisper
question is the agent's own words to a person, and it stays too; an agent that
quotes customer content into a question puts that content in the log, and a
deployment that needs it removed strips the `question` field before loading.

## Identity

Server-minted identifiers are returned in the *result* of a call, and the
audit log records the envelopes. A task id therefore becomes visible when a
later envelope acts on it, and which creation produced which id is worked out
afterwards.

With the snapshot, the pairing is settled by what the creation envelope and
the stored task agree they are, with the order of appearance breaking ties.
From envelopes alone, the order of appearance is the evidence: it settles the
pairing when work proceeds one task at a time, and leaves it open when two
tasks are created before either is touched. The row says which case it is in.
`id_certain` is true where the evidence leaves one reading, so

```python
f.tasks[f.tasks.id_certain]
```

is the population to draw conclusions about individual tasks from. Counts hold
either way: a creation that matches no observed id is given one marked
`unidentified`, and the total stays right.

The protocol settles more than the ordering can. A lapse notice concerns a
whisper whose deadline had passed. An answer comes from someone the whisper was
addressed to. A vote comes from someone the deliberation invited. An acceptance
comes from the named recipient. An id the caller supplied is in the opening
envelope itself. Each of those is used before the ordering is consulted, and
`frames.summary()` reports how many rows remain inferred.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The fixtures drive a real coordinator. A generated fixture captures what the
coordinator does, and fails when the protocol moves.

Alongside the written cases there is a differential suite: random sequences
of accepted calls against a real coordinator, read both ways. The
tasks, decisions, overrides, whispers, deliberations and handoffs are checked
against what that coordinator holds, the corrected artefacts against the ones
it stored, and a floor is asserted on how many envelope-only rows are certain.

## Licence

Apache 2.0.
