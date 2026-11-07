# chap-llama-index

Bridge between [LlamaIndex Workflows](https://developers.llamaindex.ai/python/framework/understanding/workflows/)
and the CHAP Coordinator. When a workflow step pauses for human input, the
bridge puts the proposed output in front of a person as a CHAP review,
waits for their decision, made at a CHAP desk, and answers the workflow
with what they decided. Every step is a hash-linked entry on the
workspace's audit log.

The proposed output on the `InputRequiredEvent`, its `proposed` field or
else all of its fields, is the artefact under review; an event that
proposes nothing raises `ValueError`, since nobody can approve what they
were not shown. The `HumanResponseEvent` the bridge returns carries the
decision.
An edit is an RFC 6902 patch on the log, so the chain records what changed
and why.

## Install

```bash
pip install chap-llama-index
```

Depends on `chap-coordinator>=0.3.4`. Install the extra to run a live
workflow; `request_response` needs it to build the event it returns:

```bash
pip install "chap-llama-index[llama-index]"
```

## Quick start

A step returns an `InputRequiredEvent` and waits; a second step consumes
the `HumanResponseEvent` that answers it. The driver that streams the
workflow's events hands each pause to `request_response`, which opens a
review of the proposed output and waits for the person's decision. This
runs offline: a task on the event loop stands in for Alice at her desk.

```python
import asyncio

from workflows import Context, Workflow, step
from workflows.events import HumanResponseEvent, InputRequiredEvent, StartEvent, StopEvent

from chap_coordinator import Coordinator
from chap_llama_index import ChapHitlBridge

coord = Coordinator(enable_chain=True)
bridge = ChapHitlBridge(
    coord,
    workspace="wsp_payments",
    agent="agent:writer#v1",
    reviewer="human:alice@example.org",
)


class PaymentReview(Workflow):
    @step
    async def propose(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
        return InputRequiredEvent(prefix="approve payment? ", proposed={"amount": 100, "to": "acct-9"})

    @step
    async def pay(self, ctx: Context, ev: HumanResponseEvent) -> StopEvent:
        if ev.get("decision") in ("approve", "override"):
            return StopEvent(result=f"paid {ev.get('response')}")
        return StopEvent(result=f"not paid: {ev.get('response')}")


async def alice_at_her_desk() -> None:
    """What a CHAP desk does for Alice: it shows her the output put to her,
    and she decides it in her own name. Here she caps the amount."""
    def call(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": {
            "workspace": "wsp_payments", "from": "human:alice@example.org", **params}})

    while not (submitted := call("audit.read", filter={"method": "task.complete"})["result"]["entries"]):
        await asyncio.sleep(0.1)
    review = submitted[0]["envelope"]["params"]
    print("Alice is shown:", review["output"])
    call("decide.override", task_id=review["task_id"], rationale="over the desk limit",
         diff=[{"op": "replace", "path": "/amount", "value": 50}])


async def main() -> None:
    desk = asyncio.create_task(alice_at_her_desk())
    handler = PaymentReview(timeout=None).run()
    async for event in handler.stream_events():
        if isinstance(event, InputRequiredEvent):
            handler.ctx.send_event(await bridge.request_response(event))
    print(await handler)
    await desk


asyncio.run(main())
```

## What the response carries

The review is on a task that requires review, so only a person's decision
completes it. The `HumanResponseEvent` carries:

- `response`: the output as decided, the person's version after an edit;
  or their note when nothing was approved;
- `decision`: `approve`, `override`, `revise` (sent back for another),
  `reject`, `abstain`, or another ending of the review;
- `user_name`, `rationale` and `tags`: who decided, their note and tags;
- `chap_task_id`: the CHAP task;
- every other field of the `InputRequiredEvent`, so a step that waits with
  `ctx.wait_for_event(HumanResponseEvent, requirements={"request_id": rid})`
  receives the response to its own request.

A workflow's own timeout, 45 seconds by default, counts the time the
person takes to decide: give it `timeout=None`, or one long enough.

## A revision

When the person sends the output back for another, `decision` is
`revise`. Propose the next output on the same task by passing the
response's `chap_task_id` back, and it opens the next round of the same
review:

```python
async for event in handler.stream_events():
    if isinstance(event, InputRequiredEvent):
        response = await bridge.request_response(event, task_id=event.get("chap_task_id"))
        handler.ctx.send_event(response)
```

where the step that proposes again copies `chap_task_id` from the
`revise` response onto its new `InputRequiredEvent`.

## Waiting, timeouts and cancellation

The bridge reads the task every two seconds (`poll_seconds`) for as long
as the reviewer takes, or until `timeout` seconds have passed, and never
holds up the event loop. After the timeout it withdraws the review, so no
decision lands later on output the workflow did not use, and raises
`TimeoutError`. Cancelling the awaiting task withdraws the review the same
way.

## At a desk, over HTTP

A desk such as `chap-coordinator serve`, or a project from
`create-chap-app --template support-desk`, keeps the coordinator in its own
process. The bridge reaches it with `chap_coordinator.client`:

```python
import json

from chap_coordinator.client import HttpCoordinator, Participant
from chap_llama_index import ChapHitlBridge

config = json.load(open("chap.config.json"))
agent_uri = config["agent"]["uri"]
http = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
bridge = ChapHitlBridge(
    Participant(http, config["workspace"], agent_uri),
    workspace=config["workspace"],
    agent=agent_uri,
    reviewer=config["humans"][0]["uri"],
)
```

The bridge joins the agent first, takes the desk's workspace as it is, and
reads each task with the desk's `GET /api/tasks/<id>`. Under the template's
`"require_signatures": "humans"` the agent calls unsigned and each person
signs at the desk. Where every call is signed, give the `Participant` the
agent's key, `Signer.load_or_create(agent_uri, "keys")`. Where people sign,
the bridge joins nobody in a person's name and records no decision for
them: the review opens once a person has joined from the desk, and each
decision is theirs, signed with their own key.

## Recording a decision made elsewhere

Where people decide in your own interface, `record_decision(input_event,
response_event, decision=...)` records the decision your code made, in the
reviewer's name:

```
decision      CHAP envelope
--------      ---------------------------
approve       decide.approve
override      decide.override   (proposed vs returned, as a diff)
reject        decide.reject
```

Workflows events carry no approve, edit or reject signal, so `decision`
says which it was: the bridge never guesses it from the response text.
`proposed`, `returned`, `rationale`, `tags`, `intent_preserved` and
`approver` default to fields of the events (`event.get(...)`), and each can
be given per call. An override is a refining edit (`intent_preserved=true`)
by default; set it `false` for a substituting edit.

It writes the agent's `task.create` and `task.complete`, then the
`decide.*` call in the approver's name: a person's `human:` URI, the
response's `user_name` or else the bridge's `reviewer`. The coordinator
holds that call and nothing more: no signature from the person, and no sign
that they saw anything but what your code says. So each recorded decision
carries `via: "bridge-record"`, which the log keeps and chap-analytics
reads, and `approved_artefact_digest`, the digest of the proposed output.
Where people sign, nothing is recorded: `record_decision` raises
`PersonRequired`, and they decide at the desk through `request_response`.

## What you get in the audit chain

The quick start yields the following, every entry carrying `prev_hash`:

```
seq=0  workspace.create
seq=1  participant.join  agent:writer#v1
seq=2  participant.join  human:alice@example.org
seq=3  task.create       agent:writer#v1   kind=workflow_output  review_required=true
seq=4  task.complete     agent:writer#v1   the output; opens the review
seq=5  decide.override   human:alice@example.org  diff=[{op:replace, path:/amount, value:50}]
```

The chain verifies externally, or anchors to a SCITT transparency service
with the `audit-scitt/1.0` profile.

## Upgrading from 0.3.4

- Lead with the desk: `request_response` opens the review and waits for
  the person's decision, as the quick start shows. `record_decision` is the
  recording mode above, refused where people sign.
- `decision` on the response says how the review ended: `revise`,
  `abstain` and other endings no longer read as `reject`. The response
  carries the input event's fields.
- A recorded decision opens its review on a task that requires one, with
  `task.create` then `task.complete`, and no `review.request`; it carries
  `via: "bridge-record"` and the output's digest. The approver must be a
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

`examples/01-approve-edit-reject.py` drives one paused workflow through a
desk's decisions, an approval, a refining edit, a substituting edit, a
rejection and a revision, against real `llama-index-workflows`, then
records a decision made in the script's own interface, and prints the
chain. `tests/test_workflow.py` runs `request_response` in real workflows,
against an in-process desk and a desk server where people sign.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `llama-index-workflows` 1.x and 2.x (optional; tested with
  llama-index-workflows 1.0.0 and 2.26.1)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
