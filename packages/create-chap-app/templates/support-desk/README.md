# Support desk

Tickets come from a CSV. The agent drafts a reply to each one, the draft waits
for your decision in the desk, and the reply as you decided it is written to
`replies/`. A rejected draft is written nowhere.

## Run it

```bash
pip install -r requirements.txt
python desk.py                  # owns the store, serves the desk and POST /chap
python agent.py tickets.csv     # in a second terminal: drafts, then waits for you
open http://127.0.0.1:8788/     # decide in the desk
```

`desk.py` creates the workspace, joins the participants named in
`chap.config.json` and keeps the SQLite store under `data/`. `agent.py`
drafts the three sample tickets and waits for each decision; with `--once`
it exits after the file is processed, and without it, it keeps watching the
file for new rows. Each decision lands in `replies/<ticket id>.json` with the
task id and who decided. The tests run with no model and no network:

```bash
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
`chap_client.py` wraps that for Python, and a framework bridge written
against an in-process coordinator runs unchanged over HTTP, because
`HttpCoordinator` answers `dispatch(envelope)` the way a `Coordinator` does:

```python
import json
from chap_client import HttpCoordinator
from chap_langgraph import ChapBridge, hil_review

config = json.load(open("chap.config.json"))
coord = HttpCoordinator("http://127.0.0.1:8788/chap")
bridge = ChapBridge(coord, workspace=config["workspace"], agent=config["agent"]["uri"], reviewer=config["humans"][0]["uri"])
chap_state = hil_review(bridge, draft, kind="draft_reply")
```

The review opens in the desk like any other; your graph reads the decided
output from `GET /api/tasks/<id>` when the state is no longer
`review_requested`. The other bridges (`chap-pydantic-ai`, `chap-llama-index`,
`chap-ag2`, `chap-google-adk`) take the same object in place of a coordinator.

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
`audit-scitt/1.0` the chain is on and the desk shows it verified.

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

`tests/` and `diff-profiles.py` script the decisions, because nobody is at
the desk when they run; the project itself never does.
