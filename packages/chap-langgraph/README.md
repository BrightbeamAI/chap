# chap-langgraph

Bridge between [LangGraph](https://github.com/langchain-ai/langgraph) and the
CHAP Coordinator. Wraps the human-in-the-loop interrupt boundary so every
review decision becomes a hash-linked, replayable audit entry, without
changing the inside of your existing graph nodes.

```
LangGraph event              CHAP envelope
-----------------            -------------------------
interrupt() called           task.complete + review.request
Command(resume=...) sent     decide.approve | decide.reject | decide.override
```

The bridge adds three lines to your existing LangGraph node. The
override path also turns reviewer edits into JSON Patches automatically,
so the audit chain captures *what changed and why*, not just *approved/
rejected*.

## Install

```bash
pip install chap-langgraph
```

Depends on `chap-coordinator>=0.2.5`. LangGraph itself is **optional**:
the bridge accepts any dict-shaped state and any opaque decision
payload, so it works in environments without LangGraph and is trivial
to unit-test.

## Quick start

```python
from chap_coordinator import Coordinator
from chap_langgraph import ChapBridge, hil_review

coord = Coordinator(default_profiles=["core/1.0", "review/1.0"])
bridge = ChapBridge(
    coord,
    workspace="wsp_drafter",
    agent="agent:drafter#v1",
    reviewer="human:alice@example.org",
)

# Inside your existing LangGraph node:
def drafter_node(state):
    draft = produce_draft(state)
    chap_state = hil_review(bridge, draft, kind="draft_response")
    return chap_state            # spread into LangGraph state, then interrupt()

# After the interrupt resumes with Command(resume=decision):
def resolve_review(state, decision):
    applied = bridge.apply_decision(state["chap_task_id"], decision)
    return {"draft": applied or state["chap_artefact"], **state}
```

The `decision` payload mirrors what a reviewer typically returns:

```python
"approve"                                           # decide.approve
"reject"                                            # decide.reject
{"action": "reject", "rationale": "..."}            # decide.reject (rich)
{"diff": [...], "rationale": "...", "tags": [...]}  # decide.override
```

## What you get in the audit chain

After one `hil_review` + `apply_decision` cycle, calling
`bridge.audit()` returns the full chain:

```
seq=0  workspace.create
seq=1  participant.join     agent:drafter#v1
seq=2  participant.join     human:alice@example.org
seq=3  task.create          kind=draft_response
seq=4  task.complete        agent:drafter#v1
seq=5  review.request       to=human:alice@example.org
seq=6  decide.override      diff=[...] rationale="..." tags=[...]
```

Every entry carries `prev_hash`, so you can verify the chain externally
or anchor it to a SCITT transparency service with the
`audit-scitt/1.0` profile.

## Examples

See `examples/` for runnable end-to-end demos:

- `examples/01-basic-review.py`: single drafter node, one human approval.
- `examples/02-override-cycle.py`: agent draft, human edits, audit chain.

## Compatibility

Tested against:

- `chap-coordinator` 0.2.5
- `langgraph` 0.2.x (optional)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
