# chap-langgraph

Bridge between [LangGraph](https://github.com/langchain-ai/langgraph) and the
CHAP Coordinator. Wraps the human-in-the-loop interrupt boundary so every
review decision becomes a hash-linked, replayable audit entry, and the
inside of your existing graph nodes stays as it is.

```
LangGraph event              CHAP envelope
-----------------            -------------------------
a draft is ready             task.create + review.request
Command(resume=...) sent     decide.approve | decide.reject | decide.override
```

The override path turns reviewer edits into JSON Patches, so the audit
chain records what changed and why, beside the approval or rejection.

## Install

```bash
pip install chap-langgraph
```

Depends on `chap-coordinator>=0.3.4`. The bridge imports neither LangGraph
nor LangChain: it accepts any dict-shaped state and any opaque decision
payload. Install the extra to run the example and the graph tests:

```bash
pip install "chap-langgraph[langgraph]"
```

## Quick start

LangGraph runs a node again from its first line when the graph resumes, so
the review is opened in one node and decided in the next. `draft` writes the
draft and opens its review with `hil_review`; it finishes before the graph
pauses, and its result, with the review's task id, is checkpointed.
`review` pauses on `interrupt()` and applies the decision when the graph
resumes. Resuming runs `review` again and `draft` not at all, so the draft
is made once and the decision lands on the draft the reviewer saw.

```python
from typing import Any, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from chap_coordinator import Coordinator
from chap_langgraph import ChapBridge, ReviewRejected, hil_review

coord = Coordinator(enable_chain=True)
bridge = ChapBridge(
    coord,
    workspace="wsp_drafter",
    agent="agent:drafter#v1",
    reviewer="human:alice@example.org",
)


class State(TypedDict, total=False):
    ticket: str
    reply: Any
    rejected: Optional[str]
    chap_task_id: str
    chap_artefact: Any
    chap_artefact_digest: str
    chap_kind: str


def draft(state: State) -> State:
    reply = {"reply": f"Thank you for writing about {state['ticket']!r}."}
    return hil_review(bridge, reply, kind="draft_response")


def review(state: State) -> State:
    decision = interrupt({"task_id": state["chap_task_id"],
                          "draft": state["chap_artefact"]})
    try:
        reply = bridge.apply_decision(state["chap_task_id"], decision,
                                      state["chap_artefact"])
    except ReviewRejected as rejected:
        return {"reply": None, "rejected": rejected.rationale}
    return {"reply": reply}


builder = StateGraph(State)
builder.add_node("draft", draft)
builder.add_node("review", review)
builder.add_edge(START, "draft")
builder.add_edge("draft", "review")
builder.add_edge("review", END)
graph = builder.compile(checkpointer=InMemorySaver())

config = {"configurable": {"thread_id": "ticket-1"}}
paused = graph.invoke({"ticket": "a late refund"}, config)
print("shown:", paused["__interrupt__"][0].value["draft"])

final = graph.invoke(Command(resume="approve"), config)
print("approved:", final["reply"])
```

The `decision` payload mirrors what a reviewer typically returns:

```python
"approve"                                           # decide.approve
"reject"                                            # decide.reject
{"action": "reject", "rationale": "..."}            # decide.reject (rich)
{"diff": [...], "rationale": "...", "tags": [...]}  # decide.override
```

## What `apply_decision` returns

`apply_decision(task_id, decision, artefact)` takes the draft the reviewer
was shown, `state["chap_artefact"]`, and returns what carries on:

- **approve:** the draft as shown;
- **override:** the reviewer's version, the draft with their JSON Patch
  applied;
- **reject:** nothing. It raises `ReviewRejected`, with the task id, the
  reviewer and their rationale, so a rejected draft goes no further. Catch it
  in the node and route, as above, or let it end the run.

Every decision carries `approved_artefact_digest`, the digest of the draft
shown, and the coordinator refuses a decision whose digest does not match
the draft under review. `hil_review` returns the digest as
`chap_artefact_digest`; an interface that sends it back with the decision,
as `{"action": "approve", "approved_artefact_digest": ...}`, binds the
decision to what it showed.

## What it records

`apply_decision` records the decision in your resume payload, made in your
own interface, as a `decide.*` call in the reviewer's name. The coordinator
holds that call and nothing more: no signature from the reviewer, and no
sign that they used a CHAP desk. The bridge joins the reviewer to the
workspace when it first records a decision for them.

## When the person decides at a desk

When the coordinator is a desk's, through a client such as the templates'
`HttpCoordinator`, the review opens in the desk and the person decides
there, signed with their own key. The bridge joins only the agent, so the
person joins at the desk with their key. In the `review` node, wake on the
interrupt, read the decision, and apply it:

```python
def review(state: State) -> State:
    interrupt({"task_id": state["chap_task_id"], "draft": state["chap_artefact"]})
    decision = bridge.wait_for_decision(state["chap_task_id"])
    return {"reply": bridge.apply_decision(state["chap_task_id"], decision,
                                           state["chap_artefact"])}
```

Resume the graph once the person has decided; until then
`wait_for_decision` waits, reading the task every two seconds
(`poll_seconds`), for as long as the reviewer takes or until `timeout`
seconds. A `Decision` read back is applied and not recorded again. With an
in-process coordinator the bridge reads the task directly; over HTTP, pass
a client whose `task(task_id)` returns the desk's `GET /api/tasks/<id>`
view as `ChapBridge(..., client=...)`; a coordinator client with
`task(task_id)` of its own needs none.

## What you get in the audit chain

After one `hil_review` and `apply_decision` cycle, `bridge.audit()` returns
the full chain:

```
seq=0  workspace.create
seq=1  participant.join     agent:drafter#v1
seq=2  task.create          kind=draft_response  review_required=true
seq=3  review.request       to=human:alice@example.org
seq=4  participant.join     human:alice@example.org
seq=5  decide.override      diff=[...] rationale="..." approved_artefact_digest=sha256:...
```

With `Coordinator(enable_chain=True)`, as in the quick start, every entry
carries `prev_hash`, so you can verify the chain externally or anchor it
to a SCITT transparency service with the `audit-scitt/1.0` profile.

## Upgrading from 0.3.4

- `apply_decision` takes a third argument, the draft the reviewer was
  shown: `bridge.apply_decision(state["chap_task_id"], decision,
  state["chap_artefact"])`.
- A rejection raises `ReviewRejected`. It used to return `None`, and the
  documented `applied or draft` carried the rejected draft on. Replace
  `applied or draft` with the return value, and catch `ReviewRejected`
  where a rejection should route elsewhere.
- An approval returns the draft shown. It used to return `None`.
- Call `hil_review` in the node that makes the draft, and `interrupt()` in
  the next. In one node, the draft is made a second time on resume and the
  decision lands on a draft the reviewer never saw.
- Return only the keys a node changes. The 0.3.4 README's
  `{"draft": applied or state["chap_artefact"], **state}` put the old draft
  back over the reviewer's version.
- `hil_review` opens the review on a task that requires one,
  `task.create` then `review.request`, and records no `task.complete`
  first. The reviewer joins when the first decision is recorded.
- `decide.approve` and `decide.reject` carry the reviewer's rationale as
  `comment`, where the desk and chap-analytics read it.

## Example

`examples/01-review-in-a-graph.py` runs the graph above with LangGraph's
fake chat model drafting each reply (offline, no API key): one draft
approved, one edited and one rejected, then the chain, verified.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `langgraph` 1.x (optional; tested with langgraph 1.2.20 and
  langchain-core 1.7.6)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
