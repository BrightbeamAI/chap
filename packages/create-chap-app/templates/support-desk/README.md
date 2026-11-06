# Support desk

Tickets come from a CSV. The agent drafts a reply to each one, the draft waits
for your decision in the desk, and the reply as you decided it is written to
`replies/`. A rejected draft is written nowhere.

## Run it

```bash
python -m pip install -r requirements.txt
python desk.py                  # owns the store, serves the desk and POST /chap
python agent.py tickets.csv     # in a second terminal: drafts, then waits for you
```

Then decide in the desk at http://127.0.0.1:8788/.

`desk.py` creates the workspace and keeps the SQLite store under `data/`
beside `chap.config.json`, wherever you start it from. Opening the desk
joins you to the workspace with a key it makes in your browser, and
`desk.py` joins the agent, whose calls need no signature (see [Signatures
and the chain](#signatures-and-the-chain)). `agent.py`
opens a task for each of the three sample tickets, drafts a reply, submits
it and waits for your decision; with `--once` it exits once every ticket in
the file is decided, and without it, it keeps watching the file for new
rows. An approved or overridden reply lands in `replies/<ticket id>.json`
with the task id and who decided; a rejected one is written nowhere. Reject
with "ask for a revision" ticked and the agent drafts again with your note
and submits the new draft. The ticket id is the task's idempotency key, so a
restarted agent finds the tasks it opened before and opens no duplicates; a
ticket whose text changed keeps its task, and a new id gets a new one. Until
a reviewer has joined the workspace the agent holds each draft and says so
once, since a review with nobody to address it to is refused. The tests run
with no model and no network; `requirements-dev.txt` adds pytest:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

## Attach your own agent and data

**Your data.** Point `agent.py` at your own export: `python agent.py
export.csv`. The file needs the columns `id`, `customer`, `subject` and
`body`; other columns are ignored, and `read_tickets` in `agent.py` is the
one place to change if yours are named differently.

**Your model.** Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `OLLAMA_URL`
and `agent.py` drafts with that model; `CHAP_MODEL_PROVIDER` chooses when
more than one is set. With none of them set, a scripted drafter writes the
reply from the ticket text and the console says so. `providers.py` makes one
HTTP call per draft and installs no vendor SDK. To draft with your own code,
replace `draft` in `agent.py`: it returns the JSON object the reviewer sees.

**Your agent.** Anything that can POST JSON can take the agent's place: send
JSON-RPC envelopes to `http://127.0.0.1:8788/chap` with `task.create`, then
`task.complete`, and poll `GET /api/tasks/<id>` until the state changes.
`desk.py` joins the agent `chap.config.json` names, and its calls need no
signature. Where every call is signed, it joins once with
`participant.join`, registering a key of its own, and signs each call as
`security-signed/1.0` describes. `chap_coordinator.client` does this for
Python, as `connect` in `agent.py` shows. The agent's `Participant` answers
`dispatch(envelope)` the way a `Coordinator` does, signing what the agent
sends when it holds a key, so a framework bridge written against an
in-process coordinator runs over HTTP unchanged:

```python
from agent import connect, desk_url, load_config
from chap_langgraph import ChapBridge, hil_review

config = load_config()
agent = connect(config, desk_url(config))
bridge = ChapBridge(agent, workspace=config["workspace"], agent=config["agent"]["uri"], reviewer=config["humans"][0]["uri"])
chap_state = hil_review(bridge, draft, kind="draft_reply")
```

Open the desk before the bridge starts: a bridge joins the reviewer it
names, and a reviewer joined without a key cannot add one by joining again.
The review opens in the desk like any other; your graph reads the decided
output from `GET /api/tasks/<id>` when the state is no longer
`review_requested`. The other bridges (`chap-pydantic-ai`, `chap-llama-index`,
`chap-ag2`, `chap-google-adk`) take the same object as their coordinator.
Each one's README says whether it opens a review and waits for the desk, or
records a decision made elsewhere, which this project refuses while people
sign: the decision would carry no signature of theirs.

## Signatures and the chain

`"require_signatures": "humans"` in `chap.config.json` means a person's
calls are signed. The first time you open the desk it makes a key in your
browser, registers it as it joins you to the workspace, and signs each
decision with it. The coordinator refuses a call in your name that your key
did not sign, so another program on this machine cannot decide as you
through `POST /chap`. The key stays in this browser's storage for the
desk's address, where a program running under your own account can still
reach it. The agent is a member that is not a person: `desk.py` joins it
when it starts, and the coordinator takes its calls unsigned. `true` asks
every caller to sign, and `agent.py` then signs with a key it makes in
`keys/`, which `.gitignore` keeps out of git. `false` asks no one, so any
program on this machine can then decide in your name;
`CHAP_REQUIRE_SIGNATURES` (`humans`, `1` or `0`) overrides the file. While
signatures are required the server adds `security-signed/1.0` to the
profiles, and says so when it starts. A workspace keeps the signature
settings it was created with, and the server says so when it starts with
others asked of it: to change them, start a new store, by moving `data/`
aside.

`"chain": true` links each entry on the log to the one before it by its
hash, so an entry changed or taken out afterwards shows when the chain is
verified, as the desk does and as `audit.verify_chain` does for any member.

## The desk, insights and analytics

The desk has three views. Review shows each draft as a letter, with Approve,
Request changes (the agent drafts again with your note), Reject, and Edit,
which approves your version and records it as an override with your
rationale. Activity reads the chain with what each call did. Insights counts
what you did with the agent's work: how often it was accepted as written,
edited, sent back and rejected, the time to a first decision, and your own
notes and rationales, each linked to its task. `j` and `k` move through the
queue, `a`, `r` and `x` decide, and `?` lists the keys.

`python analytics.py` goes further with `chap-analytics`, the package that
reads a CHAP chain into documented tables. It reads the store under `data/`
and writes the package's interactive report, the evaluation cases (each
corrected draft with the agent's version and yours) and a refinement page
with the corrections ranked to `analytics/`, which the desk links from
Insights. Run `pip install "chap-analytics>=0.2.5"` first; `--watch 300` keeps the
pages current. A correction that recurs is a rule for the agent's prompt in
`agent.py`.

## What each profile changes in this project

The profile list in `chap.config.json` is the one the workspace advertises.
A method a profile owns is refused with `-32601` when the profile is absent.

- `core/1.0`: the workspace, its participants, the tasks and the chain, with
  `audit.read` to read it back; on its own it has no decision call, so a
  draft that asked for review waits and the reviewer's `decide.approve` is
  refused with `-32601`.
- `review/1.0`: a draft waits in the desk; the agent's own approval is
  refused with `-32011` and recorded; an override's patch is applied by the
  coordinator and the patched result is what reaches `replies/`.

Adding a profile is one edit to `chap.config.json`. With `modes/1.0` a
trial-mode task requires review whatever the agent passes; with
`control/1.0` a paused agent is assigned nothing (`-32063`); with
`audit-scitt/1.0` the chain stays on whatever `"chain"` says, and the
workspace can hand its entries to a transparency service.

## diff-profiles

```bash
python diff-profiles.py --against core/1.0
```

runs the project's workload under the configured profiles and under
`core/1.0` alone, and prints each call with its outcome under both:

```
The agent approves its own work                               refused -32011          refused -32601   <- differs
```

`--profiles` sets the first list, `--against` the second, and `--json` prints
the rows as a document for `docs/profile-explorer.md`.

## What the deployment supplies

This project does not notify anyone: the desk lists the open reviews
addressed to the reviewer it is asked about, and delivering a review to the
people named on it is the deployment's job (SPECIFICATION 15.1). It runs one
coordinator process over one SQLite file; a second process over the same
file is not supported, and more than one coordinator means partitioning
workspaces between them. The chain head stays in that file, where the
operator of this process can rewrite it; a receipt from a transparency
service, or a head published somewhere the operator cannot change, is what
shows an entry existed independently of the coordinator (SECURITY.md).

The desk, `POST /chap` and the read API under `/api/` have no login. A
`human:` URI in the desk is a label that says who is deciding; the key the
desk signs with is what ties a decision to its holder. The server listens on
the loopback address, refuses a request from another origin or under a host
name it was not given, takes JSON only on `POST /chap`, and tells the
browser that no other page may frame the desk, so a page open elsewhere
cannot decide as the reviewer. The deployment puts TLS and its own login in
front, and names the host the proxy passes in `allowed_hosts` in
`chap.config.json` or in `CHAP_ALLOWED_HOSTS`.

`tests/` and `diff-profiles.py` script the decisions, because nobody is at
the desk when they run; the project itself never does.
