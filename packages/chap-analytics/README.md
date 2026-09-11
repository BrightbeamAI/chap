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
before and the after are reconstructed from envelopes alone. A test asserts
that an envelope-only read produces the same tables as a stateful one.

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
rule the coordinator applies, so "how long did a decision take" measures the
decision that actually ended it.

**Counts tasks nobody created.** `escalate.raise` and `control.supersede` mint
a successor server-side, with no `task.create` envelope. A count that only
looks for creations is short.

**Keeps the orphans.** A task created and never touched again still gets a
row.

## Redaction

Artefacts hold whatever the agent was working on: customer messages,
contracts, source code. Pass a redactor and it sees every artefact, in both
the envelope stream and the snapshot, before anything reaches a table.

```python
from chap_analytics import from_sqlite, redact_artefacts, frames

f = frames(from_sqlite("./chap.db", redact=redact_artefacts))
```

The shape of an analysis survives redaction: counts, rates, latencies, tags,
policy references and patch paths are all metadata. Only the content goes.

## A caveat worth reading

Server-minted identifiers are returned in the *result*, and the audit log
records envelopes, not results. A task id therefore becomes visible only when
some later envelope acts on it.

With server state the pairing of creations to ids is exact. Without it, the
library infers it from order, which is sound for work that proceeds one task
at a time and can mismatch attributes across heavily interleaved tasks.
Anything it cannot pair is given an id marked `unidentified` rather than
dropped, so a count is never quietly short.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The fixtures drive a real coordinator rather than loading recorded JSON. A
recorded fixture captures what someone believed the coordinator does; a
generated one captures what it does, and fails honestly when the protocol
moves.

## Licence

Apache 2.0.
