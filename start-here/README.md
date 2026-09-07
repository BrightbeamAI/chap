# The starter

A local review desk and a small helper, so the shape of a reviewed workflow is
visible before you read the specification. Everything here is example code:
copy it, change it, delete the parts you do not need. It is not a published SDK
and it introduces nothing to the wire format.

```sh
python3 start-here/start.py
```

Python 3.10 or newer. Inside the checkout the coordinator loads from
`packages/coordinator-py` and nothing needs installing. From a copy of this
folder elsewhere, `pip install chap-coordinator` first.

## What runs

| Command | What it shows |
| --- | --- |
| `python3 start.py` | The browser review desk. Draft, edit, reason, result, chain. |
| `python3 hello.py` | The same three steps in twelve lines of Python. |
| `python3 guarded_tool.py` | A human gate in front of a function call. |
| `python3 resume.py` (twice) | A pending review surviving a process exit. |
| `node agent.mjs` | The same boundary from Node, with the desk running. |

Run them from the repository root, or from this folder with the paths
adjusted. `hello.py`, `guarded_tool.py` and `resume.py` read your decision from
the terminal; press Enter without choosing and nothing is authorised.

Only `agent.mjs` needs Node, version 18 or newer for its built-in `fetch`.

## The helper

Three calls, and each one maps to a CHAP method you can go and read about.

```python
from chap_starter import ReviewGate, ReviewPending, ReviewRejected

with ReviewGate() as chap:
    task = chap.propose({"text": "..."})       # task.create + review.request
    chap.decide(task, "edit",                   # decide.override
                edited={"text": "..."},
                rationale="why you changed it")
    reviewed = chap.result(task)                # the exact approved object
```

`propose` takes any JSON object. `decide` takes `approve`, `edit` or `reject`,
and requires a reason for the last two. `result` returns the reviewed object,
or raises `ReviewPending` or `ReviewRejected`, and never falls back to the
draft. `audit()` returns the envelopes; `verdict()` returns the chain verdict as
data; `export(path)` writes both to a file someone else can check.

Persistence is one argument: `ReviewGate(db="chap.db")`. One process owns one
database, because the store contract is single-writer.

## Two ways to put a draft in front of a person

**`review.request`**, which is what this starter uses. The task goes from
`created` to `review_requested` and the draft is held as the artefact under
review. `output` stays empty until a decision fills it, so nothing in the
chain claims the work finished before anyone looked. `hello.py` prints the
chain: `task.create`, `review.request`, `decide.override`.

**`review_required` on `task.create`**, where `task.complete` opens the review
itself rather than completing. The Coordinator addresses that review to the
human members other than the completer and the assignee, and refuses the
completion if there are none. Reach for it when the protocol should insist a
person sees the work.

Completing a task and then requesting review of its output also works, and is
what the framework bridges do. It is not what the starter shows, because it
puts a completion in the chain before anyone has looked.

Full transition table:
[SPECIFICATION.md §8.1](../SPECIFICATION.md#81-lifecycle).

## What the desk is, and is not

Real: the coordinator, the participants, the tasks, the JSON Patches, the
decisions, the SQLite storage, the hash-linked log, the chain verification, and
the evidence you can download. Fixtures: the drafts. No model is called.

Two capabilities keep the two roles apart on one machine. The reviewer
capability is printed once in the terminal and travels in the URL fragment, so
it never reaches the server log. The agent capability is written to
`.data/agent.json`, mode 0600 where the filesystem has POSIX modes. Windows
gives it the default ACL instead, so there treat it as readable by your user
account and nothing stronger.

All of this is a teaching device. A `human:` URI labels a participant; it does
not authenticate a person, and the demo is not an identity system. For
decisions that must be non-repudiable, read
[`profiles/security-signed.md`](../profiles/security-signed.md).

The HTTP API here is a convenience for the demo, not the CHAP wire protocol.
For that, run a [reference server](../reference/) or the published
[MCP server](../examples/drive-chap-from-claude-desktop.md).

## What you see is what you sign

A CHAP decision carries a digest over the artefact, so the chain proves what
was decided. It cannot prove what the decider saw.

`JSON.stringify` and most terminals pass U+202E RIGHT-TO-LEFT OVERRIDE and the
zero-width characters through untouched. A draft can therefore render as
`100.00 USD`, hash as `1.00 USD`, and produce a perfectly honest audit record
of a human approving text they never read.

The desk renders every agent-authored string through `web/render.mjs`, which
draws each invisible character as its name in a box and warns above the draft
when one is present. Try it: paste `{"amount": "100‮00.1‬ USD"}` into
the "your own JSON" box. If you build your own reviewer surface, do something
equivalent; the protocol cannot do it for you.

## Tests

From the repository root:

```sh
python3 -m unittest discover -s start-here/tests   # the helper and the API
node --test start-here/tests/render.test.mjs       # hidden characters

npm --prefix start-here/tests install              # jsdom, for the last one
node --test start-here/tests/desk.test.mjs         # the desk in a real DOM
```

The desk test builds the document from the page the server serves and lets the
shipped `web/app.js` drive it, so it exercises the same file the browser loads
rather than a copy. It skips itself when jsdom is absent.

## Boundaries

No hosted deployment, no production authentication, no external transparency
service, no live model call, no email sent, and no exactly-once tool execution.
The demo sends no telemetry and writes only to `start-here/.data/`,
which is ignored by git. Delete that folder to start over.
