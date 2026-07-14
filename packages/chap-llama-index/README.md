# chap-llama-index

Adapter between [LlamaIndex Workflows](https://developers.llamaindex.ai/python/framework/understanding/workflows/)
and the CHAP Coordinator. When a workflow step pauses for human input,
the human's decision -- approve, edit, or reject -- becomes a hash-linked,
replayable CHAP audit entry.

```
decision      CHAP envelope
--------      ---------------------------
approve       decide.approve
override      decide.override   (proposed vs returned, as a diff)
reject        decide.reject
```

The proposed output on the `InputRequiredEvent` is the artefact under
review; the human's `HumanResponseEvent` is the decision on it. An edit is
recorded as an RFC 6902 diff, so the chain captures *what changed and why*,
not just *approved/rejected*.

## Install

```bash
pip install chap-llama-index
```

Depends on `chap-coordinator>=0.2.7`. LlamaIndex is **optional**: the
adapter reads events structurally, so the bridge and its tests work
without it installed. Install the extra to run a live workflow:

```bash
pip install "chap-llama-index[llama-index]"
```

## Quick start

Workflows surface human-in-the-loop as events: a step returns an
`InputRequiredEvent` and waits; a second step consumes the matching
`HumanResponseEvent`. The driver that answers the pause is where both
events meet, so that is where the decision is recorded:

```python
from workflows.events import InputRequiredEvent, HumanResponseEvent
from chap_coordinator import Coordinator
from chap_llama_index import ChapHitlBridge

bridge = ChapHitlBridge(
    Coordinator(),
    workspace="wsp_payments",
    agent="agent:writer#v1",
    reviewer="human:alice@example.org",
)

handler = workflow.run()
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
await handler
```

## Decision is explicit

Workflows events are schemaless and carry no approve/edit/reject signal,
so `decision` is a required argument -- the adapter never guesses intent
from the response text. `proposed`, `returned`, `rationale`, `tags`,
`intent_preserved`, and `approver` default to fields read off the events
(`event.get(...)`) and can each be overridden per call:

```python
bridge.record_decision(input_event, response_event, decision="approve")
```

`intent_preserved` defaults to `true` on an override (a refining edit);
set it `false` on the response for a substituting edit -- a different
decision, not a refinement.

## Approver identity

CHAP has no ambient actor: the decider is whatever `from` the envelope
carries. The bridge uses its `reviewer` by default, but the
`HumanResponseEvent`'s `user_name` (or an explicit `approver=`) overrides
it, and the adapter joins that approver -- with a participant type taken
from the URI scheme -- before recording. Each decision is its own task
whose review is addressed to that approver, so the record satisfies the
Coordinator's authorisation rules.

## What you get in the audit chain

One paused step with an edit yields:

```
seq=3  task.create     agent:writer#v1
seq=4  task.complete   agent:writer#v1
seq=5  review.request  agent:writer#v1   to=human:sam@example.org
seq=6  decide.override human:sam@example.org  diff=[{op:replace, path:/amount, value:50}]
```

Every entry carries `prev_hash`, so the chain verifies externally or
anchors to a SCITT transparency service with the `audit-scitt/1.0`
profile.

## Example

`examples/01-approve-edit-reject.py` drives one paused workflow through
approve, a refining edit, a substituting edit, and a reject against real
`llama-index-workflows`, and prints the resulting chain.

## Compatibility

- `chap-coordinator` 0.2.7
- `llama-index-workflows` 1.x–2.x (optional; verified against 2.22)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
