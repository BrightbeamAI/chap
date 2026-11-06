# chap-google-adk

Adapter between [Google ADK](https://google.github.io/adk-docs/) and the
CHAP Coordinator. When an ADK run pauses for tool confirmation, the adapter
puts the paused call in front of a person and waits for their decision, or
records a confirmation your own code made. Either way the decision
(approve, edit, or reject) becomes a hash-linked, replayable CHAP audit
entry, and the run carries out what the chain records.

The paused tool call (name + args) is the artefact under review; the
`ToolConfirmation` that resumes the run is the decision on it. An edit is
recorded as an RFC 6902 diff, so the chain records what changed and why,
beside the approval or rejection.

## Install

```bash
pip install chap-google-adk
```

Depends on `chap-coordinator>=0.3.4`. Install the extra to run a live
agent; `request_confirmation` needs it to build the confirmation it
returns:

```bash
pip install "chap-google-adk[google-adk]"
```

## Edits reach the tool through the payload

ADK runs a confirmed call with the arguments the model proposed. The
reviewer's arguments travel in the confirmation's payload, as
`ToolConfirmation(confirmed=True, payload={"args": {...}})`, and
`apply_reviewed_args`, given to the agent as its `before_tool_callback`,
runs the call with them. Both ways of deciding below rely on it:

```
CHAP decision                 ToolConfirmation
---------------------------   ----------------------------------------
decide.approve                confirmed=True
decide.override (the args)    confirmed=True, payload={"args": {...}}
decide.reject                 confirmed=False
```

A payload with no `args` is the tool's own data, as when a leave tool asks
for `approved_days`, and `apply_reviewed_args` leaves the call as it is.

## Quick start

This runs offline: a scripted model proposes the transfer, and your code
makes the confirmation, here an edit that caps the amount.

```python
import asyncio

from google.genai import types
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.adk.tools.tool_confirmation import ToolConfirmation

from chap_coordinator import Coordinator
from chap_google_adk import ChapConfirmationBridge, apply_reviewed_args


def transfer(amount: int, to: str) -> str:
    return f"sent {amount} to {to}"


class ScriptedLlm(BaseLlm):
    """Proposes the transfer, then closes once the tool has answered."""

    async def generate_content_async(self, llm_request, stream=False):
        if any(part.function_response for content in llm_request.contents
               for part in (content.parts or [])):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(
                function_call=types.FunctionCall(name="transfer", args={"amount": 100, "to": "Alice"}))]))


async def main() -> None:
    coord = Coordinator(enable_chain=True)
    bridge = ChapConfirmationBridge(coord, workspace="wsp_payments",
                                    agent="agent:assistant#v1", reviewer="human:alice@example.org")
    agent = LlmAgent(name="pay", model=ScriptedLlm(model="scripted"),
                     tools=[FunctionTool(transfer, require_confirmation=True)],
                     before_tool_callback=apply_reviewed_args)
    runner = Runner(app_name="pay", agent=agent, session_service=InMemorySessionService())
    await runner.session_service.create_session(app_name="pay", user_id="u", session_id="s")

    paused = None
    async for event in runner.run_async(user_id="u", session_id="s", new_message=types.Content(
            role="user", parts=[types.Part(text="pay Alice 100")])):
        for part in (event.content.parts if event.content else []):
            if part.function_call and part.function_call.name == "adk_request_confirmation":
                paused = part.function_call

    # Your code makes the confirmation, the bridge records it, and the same
    # confirmation resumes the run.
    confirmation = ToolConfirmation(confirmed=True,
                                    payload={"args": {"amount": 50, "to": "Alice"}})
    bridge.record_decision(paused.args["originalFunctionCall"], confirmation,
                           rationale="over the desk limit")
    async for event in runner.run_async(user_id="u", session_id="s", new_message=types.Content(
            role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=paused.id, name="adk_request_confirmation",
                response=confirmation.model_dump()))])):
        for part in (event.content.parts if event.content else []):
            if part.function_response:
                print(part.function_response.response)
    print([e["envelope"]["method"] for e in bridge.audit()])


asyncio.run(main())
```

## Two ways to decide

**`await bridge.request_confirmation(tool_call)` puts the call in front of
a person.** Pass it the paused call, `paused.args["originalFunctionCall"]`.
It opens a review on a task that requires review, so only a person's
decision completes it, and the person decides at a CHAP desk with their own
key. It waits for the decision and returns the `ToolConfirmation` to resume
with, in the table above:

```python
confirmation = await bridge.request_confirmation(paused.args["originalFunctionCall"])
```

An edit to anything but the arguments, such as the tool name, is not
confirmed, since the run cannot apply it. The bridge reads the task every
two seconds (`poll_seconds`) for as long as the reviewer takes, or until
`timeout` seconds, when it raises `TimeoutError`.

**`bridge.record_decision(tool_call, confirmation)` records a confirmation
your own code made**, as in the quick start, and records what that
confirmation will make ADK do: not confirmed is a rejection; confirmed with
`args` in the payload that differ from the call's is an override to those
arguments; confirmed otherwise is an approval. A `decision`, `returned` or
`diff` that disagrees with the confirmation raises `ValueError`, and
nothing is recorded, so the chain never holds an edit the tool did not run.

It writes the agent's request and the decision together: `task.create`,
`task.complete` and `review.request` from the agent, then the `decide.*`
call in the approver's name. The chain holds what your code passed and
nothing more: no signature from the approver, no sign that they saw the
call, and no time between the request and the decision. Use it where your
own interface is where people decide; use `request_confirmation` to have
the decision made in CHAP.

## Request and wait over HTTP

With a desk from `create-chap-app`, the coordinator runs in the desk
process. The templates' `chap_client.py` reaches it: `HttpCoordinator`
answers `dispatch` and `Participant` reads a task with `task(task_id)`,
which the bridge takes as `client`:

```python
import json

from chap_client import HttpCoordinator, Participant
from chap_google_adk import ChapConfirmationBridge

config = json.load(open("chap.config.json"))
coord = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
agent_uri = config["agent"]["uri"]
bridge = ChapConfirmationBridge(
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
carries. `record_decision` records in the bridge's `reviewer`'s name by
default; pass a per-decision `approver` (a `human:` URI) to override it.
The adapter joins whoever it records for before the first decision in
their name, with the participant type taken from the URI scheme. Each
decision is its own task whose review is addressed to that approver, so
the record satisfies the Coordinator's authorisation rules.
`intent_preserved` defaults to `true` on an override; set it `false` for a
substituting edit.

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
seq=6  decide.override   human:alice@example.org  diff=[{op:replace, path:/args/amount, value:50}]
```

With `request_confirmation`, the agent writes `task.create` (with
`review_required`) and `review.request`, and the person's decision follows
from the desk, whenever they make it. The chain verifies externally or
anchors to a SCITT transparency service with the `audit-scitt/1.0` profile.

## Upgrading from 0.3.4

- Give the agent `before_tool_callback=apply_reviewed_args`, and resume an
  edited call with `ToolConfirmation(confirmed=True, payload={"args": ...})`.
  Under 0.3.4 the README and the example recorded edits passed as
  `returned` and resumed with `{"confirmed": True}` alone, so ADK ran the
  model's arguments and the chain recorded an edit that never ran.
- `record_decision` reads the edit from the confirmation's payload. An
  override the confirmation does not carry, and a `decision` that
  disagrees with `confirmed`, raise `ValueError`.
- The bridge no longer joins the reviewer when it is made. It joins them
  before the first decision it records in their name, so a reviewer who
  decides at a desk joins there with their own key.

## Example

`examples/01-approve-edit-reject.py` drives one confirmation-gated tool
through approve, a refining edit, a substituting edit, and a reject against
real `google-adk` (offline, no API key), printing what each transfer ran
with, then the chain. `tests/test_runner.py` runs `request_confirmation`
with a reviewer deciding at an in-process desk.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `google-adk` 1.29 and later (optional; tested with google-adk 2.11.0 and
  google-genai 2.33.0)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
