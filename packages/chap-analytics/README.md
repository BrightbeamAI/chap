# chap-analytics

A CHAP audit chain, as pandas tables.

A CHAP chain records what humans decided about agent work: what an agent
produced, what a person changed, why, under which rule, and when. That is a
continuously generated, human-labelled evaluation set with provenance and
counterfactuals. This package projects it into documented tables so it can be
analysed as one.

Stage one of [`ANALYTICS_ROADMAP.md`](../../ANALYTICS_ROADMAP.md). It stops at
the tables on purpose: statistics belong in a layer above, where their
assumptions can be stated, and a chain with twelve decisions in it will not
support most of them.

## Install

```bash
pip install chap-analytics
```

Python 3.10+. `pandas>=2.0` is the only required dependency. Reading a chain
out of a running `Coordinator` in-process also needs `chap-coordinator`, which
is the `coordinator` extra; reading a SQLite file, a JSON export or an HTTP
endpoint needs nothing further.

## Quick start

```python
from chap_analytics import from_sqlite, frames

chain = from_sqlite("./chap.db", workspace="wsp_support")
f = frames(chain)

print(f.summary())

# Which part of the output do reviewers keep correcting?
f.patch_ops.groupby("top_path").size().sort_values(ascending=False)

# Does the agent's confidence mean anything?
f.tasks[f.tasks.confidence.notna()].groupby("outcome")["confidence"].describe()

# Refining the agent's decision, or reversing it?
f.overrides.intent_preserved.value_counts(dropna=False)
```

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

Every column is declared in `schema.py` with its dtype and its provenance.
`describe()` prints the whole contract:

```python
from chap_analytics import describe
print(describe())
```

## Four sources, and what each one knows

```python
from chap_analytics import from_sqlite, from_url, from_json, from_coordinator

from_sqlite("./chap.db", workspace="wsp_support")     # richest
from_url("http://localhost:8080/chap", "wsp_support") # envelopes only
from_json("export.json")
from_coordinator(coord, workspace="wsp_support")      # in-process
```

The two kinds of source do not carry the same information, and the library
says so rather than returning nulls without explanation.

`audit.read`, which is all an MCP client can obtain, returns envelopes: every
request parameter, in order, hash-linked. It does not return what the server
computed, so deliberation outcomes and routing outcomes come back null.

A SqliteStore file or a live `Coordinator` carries the full snapshot as well.

Almost everything worth analysing is available either way, because the chain
is **replayed** rather than read out of server state. The artefact under
review arrives on `review.request` and the patch on `decide.override`, so the
before and the after are reconstructed from envelopes alone.

How far the two agree is measured rather than asserted. Random workspaces are
driven against a real coordinator and read both ways, and for every task,
decision, override, whisper, deliberation and handoff the row either matches
what the coordinator holds or is marked `id_certain` false. The one thing an
envelope-only read cannot always recover is which server-minted id belongs to
which creation, and the caveat below says what that costs.

A column the source could not populate is present and null, never absent, so
code can reference any column without first asking where the chain came from.

## What it does for you that a notebook would get wrong

**Parses `confidence`.** Fractional values travel the CHAP wire as decimal
strings, because canonicalisation admits only integers (SPECIFICATION §7). A
notebook that forgets gets a column of strings and a statistic that silently
means nothing.

**Censors open work.** `lifetime_s` is null while a task is unsettled rather
than zero or omitted. A mean that quietly drops open reviews flatters every
latency claim ever made from it.

**Knows when a decision settled a review.** Under `all_approve` or `quorum:N`
an approval may leave the review open. `is_final` is computed with the same
rule the coordinator applies, down to the detail that `all_approve` waits only
on the reviewers it can name: a review addressed to a group has no bounded set
to wait on, so the coordinator degrades it to first-approve, and counting the
group URI as one more reviewer would leave the review open forever.

**Separates the review passes.** A task sent back for revision is reviewed
again, and the coordinator starts that review with no decisions in it. Pooling
the passes reports a quorum assembled over two different artefacts as though it
had been assembled over one, and marks a decision as final that settled
nothing. `review_index` says which pass a decision belongs to, and `latency_s`
is measured from that pass's own opening.

**Reconstructs the correction.** `based_on` is the artefact the reviewer saw
and `result` is the patch applied to it, with the coordinator's own RFC 6902
semantics, so the before and the after of every override are available to a
client that has only `audit.read`. Where state carries the artefact the
coordinator stored, the two are required to agree.

**Counts tasks nobody created.** `escalate.raise` and `control.supersede` mint
a successor server-side, with no `task.create` envelope, and give it what the
original had where the spec says nothing: its kind and mode for an escalation,
its assignee and mode for a supersession, and the same review rule a creation
gets. A count that only looks for creations is short, and a successor replayed
as a bare creation completes where the coordinator opens a review.

**Keeps the orphans.** A task created and never touched again still gets a
row.

**Says what it is unsure about.** `id_certain` and `assignee_certain` mark the
rows where the chain admits more than one reading, so a filter is available
where today the alternative is a footnote nobody reads.

## Redaction

Artefacts hold whatever the agent was working on: customer messages,
contracts, source code. Pass a redactor and it sees every one of them before
anything reaches a table.

```python
from chap_analytics import from_sqlite, redact_artefacts, frames

f = frames(from_sqlite("./chap.db", redact=redact_artefacts))
```

What goes: task inputs and outputs, the artefact under review and the
corrected one, the values a patch writes, free-text whisper answers under
either of their two names, lapse defaults, the inputs of a successor an
escalation or supersession mints, the copies a snapshot holds, and all of it in
both the envelope stream and the workspace snapshot that also contains it. A
redactor that leaves content one attribute away is worse than no redactor,
because someone relied on it, so a test plants a marker in each of those
places and searches every cell of every table for all of them.

What stays, because it is the analysis rather than the material: counts,
rates, latencies, tags, policy references, patch paths and operations, which
option a whisper answer chose, and the words a reviewer wrote about their
decision. A rationale, a comment, a question, a decline reason and a handoff
summary are the reviewer's account of what they did, and the point of the
chain; the redactor is not shown them.

## A caveat worth reading

Server-minted identifiers are returned in the *result*, and the audit log
records envelopes, not results. A task id therefore becomes visible only when
some later envelope acts on it, and which creation produced which id has to be
worked out afterwards.

Server state settles it, by what the creation envelope and the stored task
agree they are. Without state the library falls back on the order ids appear
in, which is forced when work proceeds one task at a time and a guess when two
tasks are created before either is touched. It does not present the two the
same way: `id_certain` is true only where the log leaves no alternative
reading, so

```python
f.tasks[f.tasks.id_certain]
```

is the population to draw conclusions about individual tasks from. Counts are
unaffected: a creation that matches no id is given one marked `unidentified`
rather than dropped, so a total is never quietly short. A stateful read marks a
row uncertain only where two tasks are indistinguishable by everything the
coordinator recorded about them and the order they appeared in settles nothing
either.

Where the protocol settles an identity, it is used rather than the ordering: a
lapse notification concerns a whisper whose deadline had passed, a vote comes
from someone the deliberation invited, an answer comes from someone the whisper
was addressed to, an acceptance comes from the named recipient, and an id the
caller supplied itself is read straight off the opening envelope.
`frames.summary()` prints how many rows are left unsure.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The fixtures drive a real coordinator rather than loading recorded JSON. A
recorded fixture captures what someone believed the coordinator does; a
generated one captures what it does, and fails honestly when the protocol
moves.

Alongside the written cases there is a differential suite: 120 random
sequences of legal calls against a real coordinator, read both ways, with the
tasks, decisions, overrides, whispers, deliberations and handoffs checked
against what that coordinator holds, the corrected artefacts compared with the
ones it stored, and a floor on how many envelope-only rows are certain so the
exemption for uncertain rows cannot grow to hide a defect. Two defects
survived a full adversarial review of the code and were found there instead.

## Licence

Apache 2.0.
