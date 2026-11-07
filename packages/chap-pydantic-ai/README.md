# chap-pydantic-ai

Bridge between [Pydantic AI](https://ai.pydantic.dev) and the CHAP
Coordinator. When a run pauses for tool approval, the bridge puts each
proposed call in front of a person as a CHAP review, waits for their
decision, made at a CHAP desk, and hands Pydantic AI the results that carry
it out: the call as proposed, the call with the person's arguments, or
nothing. Every step is a hash-linked entry on the workspace's audit log.

The proposed call, its tool name, arguments and `tool_call_id`, is the
artefact under review. An edit of the arguments is an RFC 6902 patch on the
log, so the chain records what changed and why.

## Install

```bash
pip install chap-pydantic-ai
```

Depends on `chap-coordinator>=0.3.5`. Install the extra to run a live
agent; `request_results` needs Pydantic AI 1.41 or later to build the
results it returns:

```bash
pip install "chap-pydantic-ai[pydantic-ai]"
```

## Quick start

A tool marked `requires_approval=True` ends the run with a
`DeferredToolRequests` output. `request_results` opens a review of each
pending call and waits for the person's decisions; the run resumes with
the `DeferredToolResults` it returns. This runs offline: `TestModel`
proposes the call, and a thread stands in for Alice at her desk.

```python
import threading
import time

from pydantic_ai import Agent, DeferredToolRequests
from pydantic_ai.models.test import TestModel

from chap_coordinator import Coordinator
from chap_pydantic_ai import ChapApprovalBridge

agent = Agent(TestModel(), output_type=[str, DeferredToolRequests])  # or "openai:gpt-4o"


@agent.tool_plain(requires_approval=True)
def transfer(amount: int, to: str) -> str:
    return f"sent {amount} to {to}"


coord = Coordinator(enable_chain=True)
bridge = ChapApprovalBridge(
    coord,
    workspace="wsp_payments",
    agent="agent:assistant#v1",
    reviewer="human:alice@example.org",
)


def alice_at_her_desk() -> None:
    """What a CHAP desk does for Alice: it shows her each call put to her,
    and she decides it in her own name. Here she caps the amount."""
    def call(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": {
            "workspace": "wsp_payments", "from": "human:alice@example.org", **params}})

    while not (submitted := call("audit.read", filter={"method": "task.complete"})["result"]["entries"]):
        time.sleep(0.1)
    review = submitted[0]["envelope"]["params"]
    print("Alice is shown:", review["output"])
    call("decide.override", task_id=review["task_id"], rationale="over the desk limit",
         diff=[{"op": "replace", "path": "/args/amount", "value": 50}])


threading.Thread(target=alice_at_her_desk).start()
result = agent.run_sync("pay the invoice")
results = bridge.request_results(result.output)
result = agent.run_sync("pay the invoice", message_history=result.all_messages(),
                        deferred_tool_results=results)
print(result.output)
```

## What the person's decision does

```
CHAP decision                        Pydantic AI resolution
---------------------------------    ---------------------------------
decide.approve                       ToolApproved()
decide.override of the arguments     ToolApproved(override_args=...)
anything else, or no decision        ToolDenied(message=...)
```

Each call is reviewed on its own task, one that requires review, so only a
person's decision completes it. The run carries out exactly what was
decided, or nothing:

- an approval runs the call as proposed, and an override runs it with the
  person's arguments, which Pydantic AI checks against the tool as it
  checks the model's: arguments the tool cannot take run nothing, and the
  model is told why;
- a rejection denies the call, with the person's note as the message the
  model reads;
- an edit to anything but the arguments, such as the tool name, or one
  that takes the arguments away, is denied: the run cannot carry it out;
- a call sent back for another, a revision, is denied with the person's
  note, and its task is declined: the model's next call is reviewed on a
  task of its own.

Each result's metadata names the CHAP task, the reviewer and the
`outcome` (approve, override, revise, reject, abstain, another ending, or
`no decision`), and the tool reads it as `ctx.tool_call_metadata`. The
reviews all open before the first wait, so the person sees them together.
The review is addressed to every person in the workspace.

## Waiting, timeouts and async code

The bridge reads each task every two seconds (`poll_seconds`) for as long
as the reviewer takes, or until `timeout` seconds have passed. A call
nobody decided in time is denied, with a message saying so, and its review
is withdrawn, so no decision lands later on a call that never ran; calls
decided in time run as decided. Interrupted, the bridge withdraws every
review still open before the interrupt propagates.

In async code, await `request_results_async`:

```python
results = await bridge.request_results_async(result.output, timeout=600)
```

It never holds up the event loop, and cancelling the awaiting task, as
`asyncio.wait_for` does on its timeout, withdraws the reviews still open.

## At a desk, over HTTP

A desk such as `chap-coordinator serve`, or a project from
`create-chap-app --template support-desk`, keeps the coordinator in its own
process. The bridge reaches it with `chap_coordinator.client`; with
`python desk.py` running in a support-desk project:

```python
import json

from chap_coordinator.client import HttpCoordinator, Participant
from chap_pydantic_ai import ChapApprovalBridge

config = json.load(open("chap.config.json"))
agent_uri = config["agent"]["uri"]
http = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
bridge = ChapApprovalBridge(
    Participant(http, config["workspace"], agent_uri),
    workspace=config["workspace"],
    agent=agent_uri,
    reviewer=config["humans"][0]["uri"],
)
results = bridge.request_results(requests)   # the reviews open in the desk
```

The bridge joins the agent first, takes the desk's workspace as it is, and
reads each task with the desk's `GET /api/tasks/<id>`. Under the
template's `"require_signatures": "humans"` the agent calls unsigned and
each person signs at the desk. Where every call is signed, give the
`Participant` the agent's key, `Signer.load_or_create(agent_uri, "keys")`.
Where people sign, the bridge joins nobody in a person's name and records
no decision for them: the reviews open once a person has joined from the
desk, and each decision is theirs, signed with their own key.

## Recording a decision made elsewhere

Where people decide in your own interface, `record_results(requests,
results)` records the resolutions your code made, one CHAP decision per
call, in the reviewer's name:

```
Pydantic AI resolution               CHAP envelope
---------------------------------    ---------------------------------
True / ToolApproved()                decide.approve
ToolApproved(override_args=...)      decide.override (a diff of the args)
False / ToolDenied(message=...)      decide.reject
```

It writes the agent's `task.create` and `task.complete`, then the
`decide.*` call in the reviewer's name. The coordinator holds that call and
nothing more: no signature from the person, and no sign that they saw
anything but what your code says. So each recorded decision carries
`via: "bridge-record"`, which the log keeps and chap-analytics reads, and
`approved_artefact_digest`, the digest of the call as proposed. Record the
same results the run resumes with, so what is recorded is what runs. Where
people sign, nothing is recorded: `record_results` raises
`PersonRequired`, and they decide at the desk through `request_results`.

The approver, rationale, tags and the refine-or-replace signal ride in
`results.metadata[tool_call_id]`:

```python
results.metadata = {call.tool_call_id: {
    "approver":         "human:sam@example.org",
    "rationale":        "over the desk limit; capped to 50",
    "tags":             ["limit-exceeded"],
    "intent_preserved": True,   # the same decision, a smaller amount
}}
```

`record_decision(call, resolution, **signal)` records one. An edit of the
arguments is a refining override (`intent_preserved=true`) by default: the
person kept the decision and changed its inputs. An edit that is a
different decision in disguise says so with `intent_preserved=False`. The
approver is a person, a `human:` URI; the bridge's `reviewer` by default.

## What you get in the audit chain

The quick start yields the following, every entry carrying `prev_hash`:

```
seq=0  workspace.create
seq=1  participant.join  agent:assistant#v1
seq=2  participant.join  human:alice@example.org
seq=3  task.create       agent:assistant#v1   kind=transfer  review_required=true
seq=4  task.complete     agent:assistant#v1   the call; opens the review
seq=5  decide.override   human:alice@example.org  diff=[{op:replace, path:/args/amount, value:50}]
```

The chain verifies externally, or anchors to a SCITT transparency service
with the `audit-scitt/1.0` profile.

## Upgrading from 0.3.4

- Lead with the desk: `request_results` opens the reviews and waits for
  the person's decisions, as the quick start shows. `record_results` is the
  recording mode above, refused where people sign.
- A recorded decision opens its review on a task that requires one, with
  `task.create` then `task.complete`, and no `review.request`; it carries
  `via: "bridge-record"` and the call's digest. The approver must be a
  person, a `human:` URI.
- The bridge joins the agent when it is made. Where nobody signs, the
  reviewer joins when the first review opens; where people sign, the
  bridge joins no person.
- The bridge checks its parties when it is made, and raises `ValueError`
  when the agent is not an `agent:` or `service:` URI, when the reviewer
  is not a person (a `human:` URI), when a `Participant` given as the
  coordinator calls as someone other than the agent, and when the
  workspace exists without `core/1.0` and `review/1.0`.
- A refusal raises `chap_coordinator.client.ChapError`, exported here as
  `ChapError`; `ChapBridgeError` names the same class. Its message reads
  `<method>: <message> (<code>)`.

## Example

`examples/01-approve-edit-deny.py` drives one approval-gated tool through
a desk's decisions, an approval, two edits and a denial, using Pydantic
AI's `TestModel` (offline, no API key), then records a decision made in the
script's own interface, and prints the chain. `tests/test_agent.py` runs
`request_results` with a real agent, against an in-process desk and a desk
server where people sign.

## Compatibility

- `chap-coordinator` 0.3.5 and later
- `pydantic-ai` 1.41 and later (optional; tested with pydantic-ai-slim
  1.41.0 and pydantic-ai 2.54.0)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
