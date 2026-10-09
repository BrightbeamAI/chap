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

`desk.py` creates the workspace in trial mode, joins the participants named
in `chap.config.json` and keeps the SQLite store under `data/`. `agent.py`
drafts the three sample messages without asking for review, and each one
waits in the desk anyway, because the workspace is in trial mode under
`modes/1.0`. With `--once` it exits after the file is processed; without it,
it keeps watching the file for new rows. The row number names the message:
the first row is `m1`, and once approved it lands in `outbox/m1.json` with
the task id and who decided; a rejected message is written nowhere. While
the agent is paused, its console shows the first `task.create` refused with
`-32063`, it waits on `workspace.describe`, and it carries on when
`resume.py` has run. The tests run with no model and no network:

```bash
python -m pytest tests -q
```

## Attach your own agent and data

**Your data.** Point `agent.py` at your own export: `python agent.py
export.csv`. The file needs the columns `to`, `subject` and `brief`; other
columns are ignored, and `read_messages` in `agent.py` is the one place to
change if yours are named differently.

**Your model.** Set `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` or `OLLAMA_URL`
and `agent.py` drafts with that model; `CHAP_MODEL_PROVIDER` chooses when
more than one is set. With none of them set, a scripted drafter writes the
body from the brief and the console says so. `providers.py` makes one HTTP
call per draft and installs no vendor SDK. To draft with your own code,
replace `draft` in `agent.py`: it returns the JSON object the approver sees.

**Your agent.** Anything that can POST JSON can take the agent's place: send
JSON-RPC envelopes to `http://127.0.0.1:8789/chap` with `task.create`, then
`task.complete`, and poll `GET /api/tasks/<id>` until the state changes.
`chap_client.py` wraps that for Python, and a framework bridge written
against an in-process coordinator runs unchanged over HTTP, because
`HttpCoordinator` answers `dispatch(envelope)` the way a `Coordinator` does:

```python
import json
from chap_client import HttpCoordinator
from chap_langgraph import ChapBridge, hil_review

config = json.load(open("chap.config.json"))
coord = HttpCoordinator("http://127.0.0.1:8789/chap")
bridge = ChapBridge(coord, workspace=config["workspace"], agent=config["agent"]["uri"], reviewer=config["humans"][0]["uri"])
chap_state = hil_review(bridge, draft, kind="outbound_message")
```

The review opens in the desk like any other; your graph reads the decided
output from `GET /api/tasks/<id>` when the state is no longer
`review_requested`. The other bridges (`chap-pydantic-ai`, `chap-llama-index`,
`chap-ag2`, `chap-google-adk`) take the same object in place of a coordinator.

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
the chain is on and the desk shows it verified.

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
`human:` URI in the desk is a label that says who is deciding; it does not
authenticate them. The server listens on the loopback address, refuses a
request from another origin or under another host name, and takes JSON
only on `POST /chap`, so a page open elsewhere cannot decide as the
reviewer; the deployment puts TLS and its own login in front, and
`security-signed/1.0` with a key per person is what ties a decision to its
holder.

`tests/` and `diff-profiles.py` script the decisions, because nobody is at
the desk when they run; the project itself never does.
