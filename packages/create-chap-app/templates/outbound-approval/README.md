# Outbound approval

The agent drafts outbound messages from a CSV of briefs. Each draft is held
until the named approver decides in the desk, and only an approved message,
as decided, reaches `outbox/`. The approver can pause the agent, and while
it is paused the coordinator assigns it nothing.

## Run it

```bash
pip install -r requirements.txt
python desk.py                   # owns the store, serves the desk and POST /chap
python agent.py messages.csv     # in a second terminal: drafts, then waits for you
open http://127.0.0.1:8789/      # decide in the desk
python pause.py                  # hold the agent; python resume.py lets it go on
```

`desk.py` creates the workspace in trial mode and keeps the SQLite store
under `data/` beside `chap.config.json`, wherever you start it from.
Opening the desk joins you to the workspace with a key it makes in your
browser, and `agent.py` joins the agent with a key of its own (see
[Signatures and the chain](#signatures-and-the-chain)). `agent.py`
opens a task for each of the three sample messages and drafts each without
asking for review, and each one waits in the desk anyway, because the
workspace is in trial mode under `modes/1.0`. With `--once` it exits once
every row in the file is decided; without it, it keeps watching the file
for new rows. A message's id is a hash of its row, `m-` and twelve hex
characters, and once approved the message lands in `outbox/<id>.json` with
the task id and who decided; a rejected message is written nowhere. Reject
with "ask for a revision" ticked and the agent drafts again with your note
and submits the new draft. The id is the task's idempotency key, so a
restarted agent finds the tasks it opened before and opens no duplicates,
and a row whose text changed is a new message. Until an approver has joined
the workspace the agent holds each draft and says so once. While the agent
is paused, its console shows the first `task.create` refused with `-32063`,
it waits on `workspace.describe`, and it carries on when `resume.py` has
run. `pause.py` and `resume.py` act as the operator's console,
`service:operator@local`, a member of its own: your key is in the desk's
browser, so a call from the command line cannot carry your signature, and
the log records the pause as the console's. The tests run with no model and
no network:

```bash
python -m pytest tests -q
```

## Attach your own agent and data

**Your data.** Point `agent.py` at your own export: `python agent.py
export.csv`. The file needs the columns `to`, `subject` and `brief`; other
columns are ignored, and `read_messages` in `agent.py` is the one place to
change if yours are named differently. A message's id comes from those
three fields, so the same row is the same message on every run.

**Your model.** Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `OLLAMA_URL`
and `agent.py` drafts with that model; `CHAP_MODEL_PROVIDER` chooses when
more than one is set. With none of them set, a scripted drafter writes the
body from the brief and the console says so. `providers.py` makes one HTTP
call per draft and installs no vendor SDK. To draft with your own code,
replace `draft` in `agent.py`: it returns the JSON object the approver sees.

**Your agent.** Anything that can POST JSON can take the agent's place: send
JSON-RPC envelopes to `http://127.0.0.1:8789/chap` with `task.create`, then
`task.complete`, and poll `GET /api/tasks/<id>` until the state changes. It
joins once with `participant.join`, registering a key of its own, and signs
each call as `security-signed/1.0` describes. `chap_coordinator.client`
does both for Python, as `connect` in `agent.py` shows. The agent's
`Participant` answers `dispatch(envelope)` the way a `Coordinator` does,
signing what the agent sends, so a framework bridge written against an
in-process coordinator runs over HTTP unchanged:

```python
from agent import connect, desk_url, load_config
from chap_langgraph import ChapBridge, hil_review

config = load_config()
agent = connect(config, desk_url(config))
bridge = ChapBridge(agent, workspace=config["workspace"], agent=config["agent"]["uri"], reviewer=config["humans"][0]["uri"])
chap_state = hil_review(bridge, draft, kind="outbound_message")
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
reach it. `agent.py` signs as well, with a key it makes in `keys/`, as do
`pause.py` and `resume.py`, and `.gitignore` keeps `keys/` out of git.
`true` asks every caller to sign, and `false` asks no one, so any program
on this machine can then decide in your name; `CHAP_REQUIRE_SIGNATURES`
(`humans`, `1` or `0`) overrides the file. While signatures are required
the server adds `security-signed/1.0` to the profiles, and says so when it
starts. A participant joined without a key cannot add one by joining again,
so to turn signatures on for a store written without them, start a new
store: move `data/` aside.

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
  `audit.read` to read it back; on its own it has no decision call, and a
  task the agent completes is completed with nobody asked.
- `review/1.0`: a draft waits in the desk; the agent's own approval is
  refused with `-32011` and recorded; an override's patch is applied by the
  coordinator and the patched result is what reaches `outbox/`.
- `modes/1.0`: the workspace is in trial mode, so every task the agent
  opens requires review whatever it passes. The mode ceiling is `trial` as
  well, so a task that asks for `production` is refused with `-32040`; the
  ceiling is enforced on every workspace, and the profile adds the trial
  rule.
- `control/1.0`: `pause.py` pauses the agent, its next `task.create` is
  refused with `-32063` and recorded, and `resume.py` lets it go on.

Adding a profile is one edit to `chap.config.json`. With `audit-scitt/1.0`
the chain stays on whatever `"chain"` says, and the workspace can hand its
entries to a transparency service.

## diff-profiles

```bash
python diff-profiles.py --against core/1.0,review/1.0
```

runs the project's workload under the configured profiles and under
`core/1.0` with `review/1.0`, and prints each call with its outcome under
both:

```
A task is assigned to the paused agent                        refused -32063          accepted, created   <- differs
```

`--profiles` sets the first list, `--against` the second, and `--json` prints
the rows as a document for `docs/profile-explorer.md`.

## What the deployment supplies

This project does not notify anyone: the desk lists the open reviews
addressed to the approver it is asked about, and delivering a review to the
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
cannot decide as the approver. The deployment puts TLS and its own login in
front, and names the host the proxy passes in `allowed_hosts` in
`chap.config.json` or in `CHAP_ALLOWED_HOSTS`.

`tests/` and `diff-profiles.py` script the decisions, because nobody is at
the desk when they run; the project itself never does.
