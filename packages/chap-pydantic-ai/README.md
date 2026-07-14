# chap-pydantic-ai

Adapter between [Pydantic AI](https://ai.pydantic.dev) and the CHAP
Coordinator. When a run pauses for tool approval, the human's
decision -- approve, edit the arguments, or deny -- becomes a
hash-linked, replayable CHAP audit entry.

```
Pydantic AI resolution            CHAP envelope
------------------------          ---------------------------
True / ToolApproved()             decide.approve
ToolApproved(override_args=...)   decide.override   (diff of the args)
False / ToolDenied(message=...)   decide.reject
```

The proposed tool call (tool name, validated args, `tool_call_id`) is
the artefact under review; the resolution is the human's decision on it.
An edited argument is recorded as an RFC 6902 diff, so the chain captures
*what changed and why*, not just *approved/denied*.

## Install

```bash
pip install chap-pydantic-ai
```

Depends on `chap-coordinator>=0.2.7`. Pydantic AI is **optional**: the
adapter reads the resolution objects structurally, so the bridge and its
tests work without it installed. Install the extra to run a live agent:

```bash
pip install "chap-pydantic-ai[pydantic-ai]"
```

## Quick start

Pydantic AI surfaces human-in-the-loop through deferred tools. A tool
marked `requires_approval=True` ends the run with a `DeferredToolRequests`
output; you resolve each pending call into a `DeferredToolResults` and
feed it back via `deferred_tool_results=`. The adapter records the
decision at that resolution point:

```python
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolApproved
from chap_coordinator import Coordinator
from chap_pydantic_ai import ChapApprovalBridge

agent = Agent("openai:gpt-4o", output_type=[str, DeferredToolRequests])

@agent.tool_plain(requires_approval=True)
def transfer(amount: int, to: str) -> str:
    return f"sent {amount} to {to}"

bridge = ChapApprovalBridge(
    Coordinator(),
    workspace="wsp_payments",
    agent="agent:assistant#v1",
    reviewer="human:alice@example.org",
)

result = agent.run_sync("pay the invoice")
if isinstance(result.output, DeferredToolRequests):
    requests = result.output

    # The human resolves each pending approval.
    results = DeferredToolResults()
    call = requests.approvals[0]
    results.approvals[call.tool_call_id] = ToolApproved(
        override_args={**call.args_as_dict(), "amount": 50},
    )

    # Record the decision, then let the agent finish.
    bridge.record_results(requests, results)
    result = agent.run_sync(
        "pay the invoice",
        message_history=result.all_messages(),
        deferred_tool_results=results,
    )
```

`record_results` walks `requests.approvals`, pairs each with its entry in
`results.approvals`, and records one CHAP decision per call. The reviewer
identity, rationale, tags, and the refine-vs-replace signal ride in
`results.metadata[tool_call_id]`:

```python
results.metadata = {call.tool_call_id: {
    "approver":         "human:sam@example.org",
    "rationale":        "over the desk limit; capped to 50",
    "tags":             ["limit-exceeded"],
    "intent_preserved": True,   # same decision, smaller amount
}}
```

To record a single decision directly, skip `record_results` and call
`record_decision(call, resolution, **signal)`.

## intent_preserved

Editing arguments defaults to a refining override (`intent_preserved=true`):
the human kept the decision and changed the inputs. That default is not
always right -- sometimes an edit is a different decision in disguise -- so
the reviewer can set `intent_preserved` explicitly through the metadata
channel and the adapter records what they say.

## Approver identity

A decision record is only useful if it names who decided. CHAP has no
ambient actor: the decider is whatever `from` the envelope carries. The
bridge uses its `reviewer` by default, but a per-decision `approver` (set
on the call or in `results.metadata`) overrides it, and the adapter joins
that approver to the workspace before recording.

## What you get in the audit chain

One run with an edited approval yields:

```
seq=3  task.create     agent:assistant#v1
seq=4  task.complete   agent:assistant#v1
seq=5  review.request  agent:assistant#v1   to=human:sam@example.org
seq=6  decide.override human:sam@example.org  diff=[{op:replace, path:/args/amount, value:50}]
```

Every entry carries `prev_hash`, so the chain verifies externally or
anchors to a SCITT transparency service with the `audit-scitt/1.0`
profile.

## Example

`examples/01-approve-edit-deny.py` drives one approval-gated tool through
all three decisions using Pydantic AI's `TestModel` (offline, no API key)
and prints the resulting chain.

## Compatibility

- `chap-coordinator` 0.2.7
- `pydantic-ai` 1.x–2.x (optional; verified against 2.0)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
