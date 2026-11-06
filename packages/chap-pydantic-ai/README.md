# chap-pydantic-ai

Adapter between [Pydantic AI](https://ai.pydantic.dev) and the CHAP
Coordinator. When a run pauses for tool approval, the adapter puts the
proposed call in front of a person and waits for their decision, or
records a decision your own code made. Either way the decision (approve,
edit the arguments, or deny) becomes a hash-linked, replayable CHAP audit
entry.

The proposed tool call (tool name, validated args, `tool_call_id`) is
the artefact under review; the resolution is the human's decision on it.
An edited argument is recorded as an RFC 6902 diff, so the chain records
what changed and why, beside the approval or denial.

## Install

```bash
pip install chap-pydantic-ai
```

Depends on `chap-coordinator>=0.3.4`. Install the extra to run a live
agent; `request_results` needs Pydantic AI 1.19 or later to build the
results it returns:

```bash
pip install "chap-pydantic-ai[pydantic-ai]"
```

## Quick start

Pydantic AI surfaces human-in-the-loop through deferred tools. A tool
marked `requires_approval=True` ends the run with a `DeferredToolRequests`
output; you resolve each pending call into a `DeferredToolResults` and
feed it back via `deferred_tool_results=`. This runs offline: `TestModel`
proposes the call, and your code resolves it.

```python
from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolApproved
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

result = agent.run_sync("pay the invoice")
requests = result.output

# Your code resolves each pending approval: here, a capped amount.
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
print(result.output)
print([e["envelope"]["method"] for e in bridge.audit()])
```

## Two ways to decide

**`request_results(requests)` puts the call in front of a person.** It
opens a review of each pending call, waits for the decisions, and returns
the `DeferredToolResults` that carry them out:

```
CHAP decision                     Pydantic AI resolution
---------------------------       ------------------------
decide.approve                    ToolApproved()
decide.override (the args)        ToolApproved(override_args=...)
decide.reject, or no approval     ToolDenied(message=...)
```

Each call is reviewed on its own task, one that requires review, so only a
person's decision completes it, and the person decides at a CHAP desk with
their own key. An edit reaches the tool as `override_args`. A denial
carries the reviewer's note to the model. An edit to anything other than
the arguments, such as the tool name, is denied, because the run cannot
apply it. Each result's metadata holds the CHAP task id and the reviewer,
which the tool reads as `ctx.tool_call_metadata`.

The reviews all open before the first wait, so the reviewer sees them
together. The bridge reads each task every two seconds (`poll_seconds`)
for as long as the reviewer takes, or until `timeout` seconds, when it
raises `TimeoutError` and the reviews stay open. In async code, run it in a
thread: `await asyncio.to_thread(bridge.request_results, requests)`.

**`record_results(requests, results)` records a decision your own code
made**, as in the quick start:

```
Pydantic AI resolution            CHAP envelope
------------------------          ---------------------------
True / ToolApproved()             decide.approve
ToolApproved(override_args=...)   decide.override   (diff of the args)
False / ToolDenied(message=...)   decide.reject
```

It writes the agent's request and the decision together: `task.create`,
`task.complete` and `review.request` from the agent, then the `decide.*`
call in the reviewer's name. The chain holds what your code passed and
nothing more: no signature from the reviewer, no sign that they saw the
call, and no time between the request and the decision. Use it where your
own interface is where people decide; use `request_results` to have the
decision made in CHAP.

## Request and wait over HTTP

With a desk from `create-chap-app`, the coordinator runs in the desk
process. The templates' `chap_client.py` reaches it: `HttpCoordinator`
answers `dispatch` and `Participant` reads a task with `task(task_id)`,
which the bridge takes as `client` (a coordinator client with
`task(task_id)` of its own needs none). In a project made with the
support-desk template, with `python desk.py` running:

```python
import json

from chap_client import HttpCoordinator, Participant
from chap_pydantic_ai import ChapApprovalBridge

config = json.load(open("chap.config.json"))
coord = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
agent_uri = config["agent"]["uri"]
bridge = ChapApprovalBridge(
    coord,
    workspace=config["workspace"],
    agent=agent_uri,
    reviewer=config["humans"][0]["uri"],
    client=Participant(coord, config["workspace"], agent_uri),
)
results = bridge.request_results(requests)   # the reviews open in the desk
```

The bridge joins the agent when it is made and joins nobody else, so the
person joins at the desk with their own key. The agent's calls are
unsigned, so a desk that requires signatures has to accept them from
agents, as the signature policy `"humans"` does.

## Recorded decisions

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

Editing arguments defaults to a refining override (`intent_preserved=true`):
the human kept the decision and changed the inputs. Sometimes an edit is a
different decision in disguise, so the reviewer can set `intent_preserved`
explicitly through the metadata channel and the adapter records what they
say.

## Approver identity

A decision record is only useful if it names who decided. CHAP has no
ambient actor: the decider is whatever `from` the envelope carries. The
bridge records in its `reviewer`'s name by default, and a per-decision
`approver` (set on the call or in `results.metadata`) overrides it. The
adapter joins whoever it records for before the first decision in their
name, with the participant type taken from the URI scheme.

## What you get in the audit chain

The quick start yields the following, with every entry carrying
`prev_hash`:

```
seq=0  workspace.create
seq=1  participant.join  agent:assistant#v1
seq=2  participant.join  human:alice@example.org
seq=3  task.create       agent:assistant#v1
seq=4  task.complete     agent:assistant#v1
seq=5  review.request    agent:assistant#v1    to=human:alice@example.org
seq=6  decide.override   human:alice@example.org  diff=[{op:replace, path:/args/amount, value:50}]
```

With `request_results`, the agent writes `task.create` (with
`review_required`) and `review.request`, and the person's decision follows
from the desk, whenever they make it. The chain verifies externally or
anchors to a SCITT transparency service with the `audit-scitt/1.0`
profile.

## Upgrading from 0.3.4

The bridge no longer joins the reviewer when it is made. It joins them
before the first decision it records in their name, so a reviewer who
decides at a desk joins there with their own key.

## Example

`examples/01-approve-edit-deny.py` drives one approval-gated tool through
all three decisions using Pydantic AI's `TestModel` (offline, no API key)
and prints the resulting chain. `tests/test_agent.py` runs
`request_results` with a real agent and a reviewer deciding at an
in-process desk.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `pydantic-ai` 1.x and 2.x for `record_results`, 1.19 and later for
  `request_results` (optional; tested with pydantic-ai 2.54.0)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
