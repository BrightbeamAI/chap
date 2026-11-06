# chap-ag2

Adapter between [AG2](https://github.com/ag2ai/ag2) (AutoGen) and the CHAP
Coordinator. At an AG2 human-input turn, the adapter puts the agent's
message in front of a person and waits for their decision, or records a
decision your own code took. Either way the decision (approve, edit, or
reject) becomes a hash-linked, replayable CHAP audit entry.

The agent message the human is responding to is the artefact under
review; the human's reply is the decision on it.

## Install

```bash
pip install chap-ag2
```

Depends on `chap-coordinator>=0.3.4`. AG2 is **optional**: the adapter
reads the message and reply as plain values, so the bridge and its tests
work without it installed. Install the extra to run a live conversation:

```bash
pip install "chap-ag2[ag2]"
```

The extra installs AG2 0.x. AG2 1.0 replaced the `autogen` package and
its `get_human_input` hook, where the quick start connects, so the extra
asks for `ag2>=0.9,<1`. With AG2 1.x, install it yourself and gate tools
with `tool_approval`, below:

```bash
pip install chap-ag2 "ag2>=1.2,<2"
```

## Quick start

The message under review and the reply meet inside `get_human_input`, so
that is where the adapter works. `self.last_message()` gives the message;
the return value is the reply. This runs offline: the assistant answers
with a fixed draft, and your code takes the reviewer's reply.

```python
from autogen import ConversableAgent, UserProxyAgent

from chap_coordinator import Coordinator
from chap_ag2 import ChapTurnBridge

coord = Coordinator(enable_chain=True)
bridge = ChapTurnBridge(coord, workspace="wsp_support",
                        agent="agent:assistant#v1", reviewer="human:alice@example.org")


def capture_from_ui(prompt):
    """Your interface supplies the reply and the intent behind it."""
    return "refund $50 to Alice", "override"


class RecordingUser(UserProxyAgent):
    def get_human_input(self, prompt, **kw):
        reply, decision = capture_from_ui(prompt)
        bridge.record_turn(self.last_message(), reply, decision=decision)
        return reply


assistant = ConversableAgent("assistant", llm_config=False,
                             default_auto_reply="Draft: refund $100 to Alice.")
user = RecordingUser("user", human_input_mode="ALWAYS", code_execution_config=False)
user.initiate_chat(assistant, message="please draft a refund", max_turns=2)
print([e["envelope"]["method"] for e in bridge.audit()])
```

## Two ways to decide

**`bridge.request_reply(message)` puts the message in front of a
person.** Call it in `get_human_input`:

```python
class ReviewedUser(UserProxyAgent):
    def get_human_input(self, prompt, **kw):
        return bridge.request_reply(self.last_message())
```

It opens a review of the message on a task that requires review, so only
a person's decision completes it, and the person decides at a CHAP desk
with their own key. It waits for the decision and returns the reply that
carries it out:

```
CHAP decision                         reply to AG2
---------------------------           -----------------------------
decide.approve                        "" (the chat carries on)
decide.override                       the reviewer's version
decide.reject, asking for a revision  the reviewer's note
any other ending                      raises ReviewRejected
```

A rejection raises `ReviewRejected` from `get_human_input`, which ends the
chat, so a message the person rejected is never taken for an approved one.
The bridge reads the task every two seconds (`poll_seconds`) for as long as
the reviewer takes, or until `timeout` seconds, when it raises
`TimeoutError`. In an async chat, call it from `a_get_human_input` through
`await asyncio.to_thread(bridge.request_reply, self.last_message())`.

**`bridge.record_turn(message, reply, decision=...)` records a decision
your own code took**, as in the quick start:

```
decision      CHAP envelope
--------      ---------------------------
approve       decide.approve
override      decide.override   (message vs reply, as a diff)
reject        decide.reject
```

It writes the agent's request and the decision together: `task.create`,
`task.complete` and `review.request` from the agent, then the `decide.*`
call in the approver's name. The chain holds what your code passed and
nothing more: no signature from the approver, no sign that they saw the
message, and no time between the request and the decision. Use it where
your own interface is where people decide; use `request_reply` to have the
decision made in CHAP.

## AG2 1.x: approve a tool call

AG2 1.x gates a tool with middleware. `bridge.tool_approval()` returns one
that puts each call of the tool in front of a person and runs it as they
decide:

```python
from ag2 import Agent, tool

from chap_coordinator import Coordinator
from chap_ag2 import ChapTurnBridge

bridge = ChapTurnBridge(Coordinator(enable_chain=True), workspace="wsp_payments",
                        agent="agent:payer#v1", reviewer="human:alice@example.org")


@tool(middleware=[bridge.tool_approval()])
def transfer(amount: int, to: str) -> str:
    """Send money."""
    return f"sent {amount} to {to}"
```

The call (tool name, arguments, call id) is the artefact under review, on
a task that requires review, so only a person's decision completes it,
made at a CHAP desk with their own key. An approval runs the call as the
model made it; an override runs it with the reviewer's arguments. Anything
else runs nothing and answers the model with the reviewer's note, and so
does an edit to anything but the arguments, which the run cannot apply.
The wait reads the task every two seconds (`poll_seconds`) for as long as
the reviewer takes, or until `timeout` seconds, when `TimeoutError` ends
the turn. Wire the bridge to a desk over HTTP as below.

## The decision is explicit

AG2's human turn is a weak signal: the same `get_human_input` loop carries
plain dialogue, edits, approvals, and "exit". Inferring intent would write
decisions the human never made, which is the one thing this record exists
to avoid. So `record_turn` takes the decision explicitly, and makes only
one inference:

- an **empty reply** means "use the agent's output" -> `decide.approve`
- any **non-empty reply with no explicit decision** records **nothing**:
  ending a chat is not a rejection, and dialogue is not an edit

```python
bridge.record_turn(message, reply, decision="override",
                   rationale="over the limit", tags=["capped"])
```

`intent_preserved` defaults to `true` on an override; set it `false` for a
substituting edit, where the reviewer's version is a different decision.

## Request and wait over HTTP

With a desk from `create-chap-app`, the coordinator runs in the desk
process. The templates' `chap_client.py` reaches it: `HttpCoordinator`
answers `dispatch` and `Participant` reads a task with `task(task_id)`,
which the bridge takes as `client`:

```python
import json

from chap_client import HttpCoordinator, Participant
from chap_ag2 import ChapTurnBridge

config = json.load(open("chap.config.json"))
coord = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
agent_uri = config["agent"]["uri"]
bridge = ChapTurnBridge(
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

## Approver identity

CHAP has no ambient actor: the decider is whatever `from` the envelope
carries. `record_turn` records in the bridge's `reviewer`'s name by
default; pass a per-turn `approver` (a `human:` URI) to override it. The
adapter joins whoever it records for before the first decision in their
name, with the participant type taken from the URI scheme. Each turn is
its own task whose review is addressed to that approver, so the record
satisfies the Coordinator's authorisation rules.

## What you get in the audit chain

The quick start yields the following, with every entry carrying
`prev_hash`:

```
seq=0  workspace.create
seq=1  participant.join  agent:assistant#v1
seq=2  participant.join  human:alice@example.org
seq=3  task.create       agent:assistant#v1
seq=4  task.complete     agent:assistant#v1
seq=5  review.request    agent:assistant#v1   to=human:alice@example.org
seq=6  decide.override   human:alice@example.org  diff=[{op:replace, path:, value:"refund $50 to Alice"}]
```

With `request_reply`, the agent writes `task.create` (with
`review_required`) and `review.request`, and the person's decision follows
from the desk, whenever they make it. The chain verifies externally or
anchors to a SCITT transparency service with the `audit-scitt/1.0`
profile.

## Upgrading from 0.3.4

- The `ag2` extra installs AG2 0.x (`ag2>=0.9,<1`). Under 0.3.4 it
  resolved to AG2 1.x, which has no `autogen` module.
- The bridge no longer joins the reviewer when it is made. It joins them
  before the first decision it records in their name, so a reviewer who
  decides at a desk joins there with their own key.

## Example

`examples/01-approve-edit-reject.py` runs a real AG2 0.x conversation (no
LLM needed) through approve, an edit, and a reject, and prints the
resulting chain. `tests/test_conversation.py` runs `request_reply` in a
real conversation, and `tests/test_tool_approval.py` runs `tool_approval`
on an AG2 1.x agent with a scripted model, each with a reviewer deciding at
an in-process desk.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `ag2` 0.9 to 0.x for `request_reply` and `record_turn`, through
  `get_human_input` (optional; tested with ag2 0.14.0)
- `ag2` 1.2 and later 1.x for `tool_approval` (tested with ag2 1.2.2)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
