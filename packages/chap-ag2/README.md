# chap-ag2

Adapter between [AG2](https://github.com/ag2ai/ag2) (AutoGen) and the CHAP
Coordinator. At an AG2 human-input turn, the human's decision -- approve,
edit, or reject -- becomes a hash-linked, replayable CHAP audit entry.

```
decision      CHAP envelope
--------      ---------------------------
approve       decide.approve
override      decide.override   (message vs reply, as a diff)
reject        decide.reject
```

The agent message the human is responding to is the artefact under
review; the human's reply is the decision on it.

## Install

```bash
pip install chap-ag2
```

Depends on `chap-coordinator>=0.2.5`. AG2 is **optional**: the adapter
reads the message and reply as plain values, so the bridge and its tests
work without it installed. Install the extra to run a live conversation:

```bash
pip install "chap-ag2[ag2]"
```

## The decision is explicit

AG2's human turn is a weak signal: the same `get_human_input` loop carries
plain dialogue, edits, approvals, and "exit". Inferring intent would write
decisions the human never made, which is the one thing this record exists
to avoid. So `record_turn` takes the decision explicitly, and makes only
one inference:

- an **empty reply** means "use the agent's output" -> `decide.approve`
- any **non-empty reply with no explicit decision** records **nothing** --
  ending a chat is not a rejection, and dialogue is not an edit

```python
bridge.record_turn(message, reply, decision="override",
                   rationale="over the limit", tags=["capped"])
```

`intent_preserved` defaults to `true` on an override; set it `false` for a
substituting edit -- a different decision, not a refinement.

## Where it hooks

The message under review and the reply meet inside `get_human_input`, so
that is where a turn is recorded. `self.last_message()` gives the message;
the return value is the reply:

```python
from autogen import UserProxyAgent
from chap_ag2 import ChapTurnBridge

bridge = ChapTurnBridge(Coordinator(), workspace="wsp_support",
                        agent="agent:assistant#v1", reviewer="human:alice@example.org")

class RecordingUser(UserProxyAgent):
    def get_human_input(self, prompt, **kw):
        reply, decision = capture_from_ui(prompt)   # your UI supplies the intent
        bridge.record_turn(self.last_message(), reply, decision=decision)
        return reply
```

## Approver identity

CHAP has no ambient actor: the decider is whatever `from` the envelope
carries. The bridge uses its `reviewer` by default; pass a per-turn
`approver` (a `human:` URI) to override it. The participant type is taken
from the URI scheme and the approver is joined before recording. Each turn
is its own task whose review is addressed to that approver, so the record
satisfies the Coordinator's authorisation rules.

## What you get in the audit chain

One reviewed message with an edit yields:

```
seq=3  task.create     agent:assistant#v1
seq=4  task.complete   agent:assistant#v1
seq=5  review.request  agent:assistant#v1   to=human:sam@example.org
seq=6  decide.override human:sam@example.org  diff=[{op:replace, path:, value:"refund $50 to Alice"}]
```

Every entry carries `prev_hash`, so the chain verifies externally or
anchors to a SCITT transparency service with the `audit-scitt/1.0`
profile.

## Example

`examples/01-approve-edit-reject.py` runs a real AG2 conversation (no LLM
needed) through approve, an edit, and a reject, and prints the resulting
chain.

## Compatibility

- `chap-coordinator` 0.2.6
- `ag2` 0.9+ (optional; verified against 0.14)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
