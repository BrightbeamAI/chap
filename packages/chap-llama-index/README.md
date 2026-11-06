# chap-llama-index

Adapter between [LlamaIndex Workflows](https://developers.llamaindex.ai/python/framework/understanding/workflows/)
and the CHAP Coordinator. When a workflow step pauses for human input, the
adapter puts the proposed output in front of a person and waits for their
decision, or records a decision your own code made. Either way the
decision (approve, edit, or reject) becomes a hash-linked, replayable CHAP
audit entry.

The proposed output on the `InputRequiredEvent` is the artefact under
review; the `HumanResponseEvent` that answers it carries the decision. An
edit is recorded as an RFC 6902 diff, so the chain records what changed and
why, beside the approval or rejection.

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

Workflows surface human-in-the-loop as events: a step returns an
`InputRequiredEvent` and waits; a second step consumes the matching
`HumanResponseEvent`. The driver that answers the pause is where both
events meet. This runs offline, with your code making the decision:

```python
import asyncio

from workflows import Context, Workflow, step
from workflows.events import HumanResponseEvent, InputRequiredEvent, StartEvent, StopEvent

from chap_coordinator import Coordinator
from chap_llama_index import ChapHitlBridge


class PaymentReview(Workflow):
    @step
    async def propose(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
        return InputRequiredEvent(prefix="approve payment? ",
                                  proposed={"amount": 100, "to": "acct-9"})

    @step
    async def pay(self, ctx: Context, ev: HumanResponseEvent) -> StopEvent:
        return StopEvent(result=ev.get("response"))


async def main() -> None:
    coord = Coordinator(enable_chain=True)
    bridge = ChapHitlBridge(
        coord,
        workspace="wsp_payments",
        agent="agent:writer#v1",
        reviewer="human:alice@example.org",
    )
    handler = PaymentReview(timeout=None).run()
    async for event in handler.stream_events():
        if isinstance(event, InputRequiredEvent):
            response = HumanResponseEvent(
                response={"amount": 50, "to": "acct-9"},   # the edited output
                decision="override",
                user_name="human:sam@example.org",
                rationale="over the desk limit; capped to 50",
                tags=["limit-exceeded"],
            )
            bridge.record_decision(event, response, decision=response.get("decision"))
            handler.ctx.send_event(response)
    print(await handler)
    print([e["envelope"]["method"] for e in bridge.audit()])


asyncio.run(main())
```

## Two ways to decide

**`await bridge.request_response(event)` puts the output in front of a
person.** It opens a review of the proposed output, waits for the decision,
and returns the `HumanResponseEvent` to send back:

```python
async for event in handler.stream_events():
    if isinstance(event, InputRequiredEvent):
        handler.ctx.send_event(await bridge.request_response(event))
```

The review is on a task that requires review, so only a person's decision
completes it, and the person decides at a CHAP desk with their own key. The
event carries `response`, the output as decided (the reviewer's version
after an edit), or the reviewer's note when nothing was approved;
`decision`, which is `approve`, `override` or `reject` (a review that ends
any other way reads as `reject`); `user_name`, the reviewer; and
`rationale`, `tags` and `chap_task_id`. The bridge reads the task every two
seconds (`poll_seconds`) for as long as the reviewer takes, or until
`timeout` seconds, when it raises `TimeoutError`. The workflow's own
timeout, 45 seconds by default, counts the time the person takes to decide,
so give the workflow `timeout=None` or one long enough.

**`bridge.record_decision(event, response, decision=...)` records a
decision your own code made**, as in the quick start:

```
decision      CHAP envelope
--------      ---------------------------
approve       decide.approve
override      decide.override   (proposed vs returned, as a diff)
reject        decide.reject
```

It writes the agent's request and the decision together: `task.create`,
`task.complete` and `review.request` from the agent, then the `decide.*`
call in the approver's name. The chain holds what your code passed and
nothing more: no signature from the approver, no sign that they saw the
output, and no time between the request and the decision. Use it where
your own interface is where people decide; use `request_response` to have
the decision made in CHAP.

## Request and wait over HTTP

With a desk from `create-chap-app`, the coordinator runs in the desk
process. The templates' `chap_client.py` reaches it: `HttpCoordinator`
answers `dispatch` and `Participant` reads a task with `task(task_id)`,
which the bridge takes as `client` (a coordinator client with
`task(task_id)` of its own needs none):

```python
import json

from chap_client import HttpCoordinator, Participant
from chap_llama_index import ChapHitlBridge

config = json.load(open("chap.config.json"))
coord = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
agent_uri = config["agent"]["uri"]
bridge = ChapHitlBridge(
    coord,
    workspace=config["workspace"],
    agent=agent_uri,
    reviewer=config["humans"][0]["uri"],
    client=Participant(coord, config["workspace"], agent_uri),
)
```

The bridge joins the agent when it is made and joins nobody else, so the
person joins at the desk with their own key. The agent's calls are
unsigned, so a desk that requires signatures has to accept them from
agents, as the signature policy `"humans"` does.

## Recorded decisions

Workflows events are schemaless and carry no approve/edit/reject signal,
so `decision` is a required argument of `record_decision`: the adapter
never guesses intent from the response text. `proposed`, `returned`,
`rationale`, `tags`, `intent_preserved`, and `approver` default to fields
read off the events (`event.get(...)`) and can each be overridden per call:

```python
bridge.record_decision(input_event, response_event, decision="approve")
```

`intent_preserved` defaults to `true` on an override (a refining edit);
set it `false` on the response for a substituting edit, where the
reviewer's version is a different decision.

## Approver identity

CHAP has no ambient actor: the decider is whatever `from` the envelope
carries. `record_decision` records in the bridge's `reviewer`'s name by
default, and the `HumanResponseEvent`'s `user_name` (or an explicit
`approver=`) overrides it. The adapter joins whoever it records for before
the first decision in their name, with a participant type taken from the
URI scheme. Each decision is its own task whose review is addressed to
that approver, so the record satisfies the Coordinator's authorisation
rules.

## What you get in the audit chain

The quick start yields the following, with every entry carrying
`prev_hash`:

```
seq=0  workspace.create
seq=1  participant.join  agent:writer#v1
seq=2  participant.join  human:sam@example.org
seq=3  task.create       agent:writer#v1
seq=4  task.complete     agent:writer#v1
seq=5  review.request    agent:writer#v1   to=human:sam@example.org
seq=6  decide.override   human:sam@example.org  diff=[{op:replace, path:/amount, value:50}]
```

With `request_response`, the agent writes `task.create` (with
`review_required`) and `review.request`, and the person's decision follows
from the desk, whenever they make it. The chain verifies externally or
anchors to a SCITT transparency service with the `audit-scitt/1.0`
profile.

## Upgrading from 0.3.4

The bridge no longer joins the reviewer when it is made. It joins them
before the first decision it records in their name, so a reviewer who
decides at a desk joins there with their own key.

## Example

`examples/01-approve-edit-reject.py` drives one paused workflow through
approve, a refining edit, a substituting edit, and a reject against real
`llama-index-workflows`, and prints the resulting chain.
`tests/test_workflow.py` runs `request_response` in a real workflow, with
a reviewer deciding at a desk on the same event loop.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `llama-index-workflows` 1.x and 2.x (optional; tested with
  llama-index-workflows 2.26.1)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
