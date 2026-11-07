# chap-langgraph

Bridge between [LangGraph](https://github.com/langchain-ai/langgraph) and the
CHAP Coordinator. A node that makes a draft puts it in front of a person as
a CHAP review; the graph waits for their decision, made at a CHAP desk, and
carries on with what they decided. Every step is a hash-linked entry on the
workspace's audit log, and the inside of your graph nodes stays as it is.

```
LangGraph                          CHAP
-----------------------------      ------------------------------------------
draft node: hil_review(...)        task.create (review required), task.complete
                                   the person decides at a desk: decide.*
review node: wait_for_decision     the decision, read back from the task
             apply_decision        the draft as decided, or ReviewRejected
```

## Install

```bash
pip install chap-langgraph
```

Depends on `chap-coordinator>=0.3.5`. The bridge imports neither LangGraph
nor LangChain: it takes dict-shaped state and returns LangGraph-shaped
values. Install the extra to run the example and the graph tests:

```bash
pip install "chap-langgraph[langgraph]"
```

## Quick start

LangGraph runs a node again from its first line when the graph resumes, so
the review is opened in one node and decided in the next. `draft` writes the
draft and opens its review with `hil_review`; the node finishes, and its
result, with the review's task id, is checkpointed. `review` pauses the
graph on `interrupt()` while the person decides. Resumed, it reads their
decision from CHAP with `wait_for_decision` and applies it: the graph
carries on with the draft as decided, and a draft the person did not approve
goes no further.

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
    return hil_review(bridge, reply, kind="draft_response", input_={"ticket": state["ticket"]})


def review(state: State) -> State:
    interrupt({"task_id": state["chap_task_id"], "draft": state["chap_artefact"]})
    decision = bridge.wait_for_decision(state["chap_task_id"])
    try:
        reply = bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"])
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
shown = paused["__interrupt__"][0].value
print("Alice is shown:", shown["draft"])

# Alice decides at her CHAP desk, in her own name. Here one call stands in
# for her desk: she approves the draft she was shown.
coord.dispatch({"jsonrpc": "2.0", "id": "alice-1", "method": "decide.approve",
                "params": {"workspace": "wsp_drafter", "from": "human:alice@example.org",
                           "task_id": shown["task_id"], "comment": "Fine to send."}})

final = graph.invoke(Command(resume=True), config)
print("sent:", final["reply"])
```

The resume value only wakes the graph: the decision is read from CHAP, so
the graph carries on with what the person decided at the desk, and nothing
in the resume can change it. A graph that may hold a worker while the
person decides can leave `interrupt()` out: `wait_for_decision` then waits in
the node, reading the task every two seconds (`poll_seconds`) for as long as
the reviewer takes, or until `timeout` seconds have passed. A wait that
stops leaves the review open, since the graph's state holds its task id and
resuming the graph waits on it again; `bridge.withdraw(task_id)` gives a
draft up, so no decision lands on it.

The review is addressed to every person in the workspace. In a coordinator
of your own that requires no signatures, `reviewer` joins it when the first
review opens.

## At a desk, over HTTP

A desk such as `chap-coordinator serve`, or a project from
`create-chap-app --template support-desk`, keeps the coordinator in its own
process. The bridge reaches it with `chap_coordinator.client`:

```python
from chap_coordinator.client import HttpCoordinator, Participant
from chap_langgraph import ChapBridge

agent = Participant(HttpCoordinator("http://127.0.0.1:8788/chap"), "wsp_support", "agent:drafter")
bridge = ChapBridge(agent, workspace="wsp_support", agent="agent:drafter", reviewer="human:you@local")
```

The bridge joins the agent first, takes the desk's workspace as it is, and
reads each task with the desk's `GET /api/tasks/<id>`. Under the templates'
`"require_signatures": "humans"`, the agent calls unsigned and each person
signs at the desk. Where every call is signed, give the `Participant` the
agent's key, `Signer.load_or_create("agent:drafter", "keys")`, and the
bridge signs each of the agent's calls with it. Where people sign, the
bridge joins nobody in a person's name and records no decision for them:
`hil_review` waits until a person has joined from the desk, and each
decision is theirs, signed with their own key.

## What `apply_decision` returns

`apply_decision(task_id, decision, artefact)` returns the draft to carry on
with, as the coordinator holds it:

- **approve:** the draft the person was shown;
- **override:** their version, the draft with their JSON Patch applied;
- anything else raises `ReviewRejected`, so a draft nobody approved goes no
  further. Its `outcome` says how the review ended: `reject`, `revise` when
  the person asked for another draft, `abstain`, or the state another
  ending left the task in. It carries the task id, the reviewer and their
  note as `rationale`. Catch it in the node and route, or let it end the
  run: it is a `RuntimeError`, which LangGraph's default retry policy does
  not retry.

## A revision

When the person asks for another draft, the task goes back in progress and
`ReviewRejected.outcome` is `revise`. Route back to the drafting node and
open the next round of the same review with `existing_task_id`:

```python
def redraft(state: State) -> State:
    reply = {"reply": produce_draft(state, note=state["rejected"])}
    return hil_review(bridge, reply, kind="draft_response",
                      existing_task_id=state["chap_task_id"])
```

## Recording a decision made elsewhere

Where the person decides in your own interface, and the graph's resume value
carries their decision, `apply_decision` records it in the reviewer's name:

```python
"approve"                                                # decide.approve
"reject"                                                 # decide.reject
{"action": "reject", "rationale": "...",
 "request_revision": True}                               # decide.reject, another draft
{"diff": [...], "rationale": "...", "tags": [...]}       # decide.override
```

The coordinator holds the call and nothing more: no signature from the
person, and no sign that they saw anything but what your interface says
they saw. So the call carries `via: "bridge-record"`, which the log keeps
and chap-analytics reads, and `approved_artefact_digest`, the digest of the
draft the graph's state holds, or the one your payload names, where your
interface sends back `chap_artefact_digest` for the draft it showed. The
coordinator refuses the decision unless that is the draft under review. A
node that runs again after recording a decision reads it back, and records
nothing twice. Where people sign, nothing is recorded:
`apply_decision` raises `PersonRequired`, and they decide at the desk.

## Parallel drafts

Drafts made in parallel branches write their keys under a prefix of their
own, so the branches do not write the same state key in one step:

```python
def draft_refund(state):
    return hil_review(bridge, refund_reply(state), kind="refund", prefix="refund_")

def draft_apology(state):
    return hil_review(bridge, apology(state), kind="apology", prefix="apology_")
```

`refund_task_id` and `refund_artefact` then go to the refund's
`wait_for_decision` and `apply_decision`. For a number of drafts known only
at run time, send each item to the drafting node with `Send`, collect the
`hil_review` results under a list key with an `operator.add` reducer, and
wait for each review in the next node.

## What you get in the audit chain

After one draft and a decision at the desk, `bridge.audit()` returns:

```
seq=0  workspace.create
seq=1  participant.join     agent:drafter#v1
seq=2  participant.join     human:alice@example.org
seq=3  task.create          kind=draft_response  review_required=true
seq=4  task.complete        the draft; opens the review
seq=5  decide.approve       from=human:alice@example.org
```

With `Coordinator(enable_chain=True)`, as in the quick start, every entry
carries `prev_hash`, so you can verify the chain externally or anchor it to
a SCITT transparency service with the `audit-scitt/1.0` profile.

## Upgrading from 0.3.4

- Lead with the desk: `hil_review`, then `wait_for_decision` and
  `apply_decision`, as the quick start shows. A resume payload recorded in
  the reviewer's name is the recording mode above, refused where people
  sign.
- `apply_decision` takes a third argument, the draft as the graph's state
  holds it: `bridge.apply_decision(state["chap_task_id"], decision,
  state["chap_artefact"])`. It returns the draft as the coordinator holds
  it, and raises `ReviewRejected` for anything not approved. In 0.3.4 a
  rejection returned `None`, and the documented `applied or draft` carried
  the rejected draft on.
- Call `hil_review` in the node that makes the draft, and wait or
  interrupt in the next. In one node, the draft is made a second time on
  resume and the decision lands on a draft the person never saw. Return
  only the keys a node changes: the 0.3.4 README's
  `{"draft": applied or state["chap_artefact"], **state}` put the old draft
  back over the person's version.
- `hil_review` opens the review on a task that requires one, with
  `task.create` then `task.complete`, so the review is addressed to every
  person in the workspace. Where nobody signs, the reviewer joins when the
  first review opens.
- The bridge checks its parties when it is made, and raises `ValueError`
  when the agent is not an `agent:` or `service:` URI, when the reviewer
  is not a person (a `human:` URI), when a `Participant` given as the
  coordinator calls as someone other than the agent, and when the
  workspace exists without `core/1.0` and `review/1.0`. An empty draft
  raises `ValueError` too, since nobody can approve what they were not
  shown.
- `create_task`, `complete_with_review`, `approve`, `reject` and `override`
  are gone. They recorded a decision in a person's name bound to nothing
  they were shown; `apply_decision` records one from your interface, bound
  to the draft.
- A refusal raises `chap_coordinator.client.ChapError`, exported here as
  `ChapError`; `ChapBridgeError` names the same class. Its message reads
  `<method>: <message> (<code>)`.
- `ReviewRejected` is a `RuntimeError`, with an `outcome`.
- `decide.approve` and `decide.reject` carry the person's note as
  `comment`, where the desk and chap-analytics read it, and no note where
  the payload gives none.

## Example

`examples/01-review-in-a-graph.py` runs the graph above with LangGraph's
fake chat model drafting each reply (offline, no API key), and a person at
a desk that shares the coordinator: one draft approved, one edited and one
sent back for another, which is approved, then the chain, verified.

## Compatibility

- `chap-coordinator` 0.3.5 and later
- `langgraph` 1.x (optional; tested with langgraph 1.0.0 and 1.2.20, and
  langchain-core 1.7.6)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
