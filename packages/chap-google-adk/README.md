# chap-google-adk

Bridge between [Google ADK](https://adk.dev/) and the CHAP Coordinator.
When an ADK run pauses for tool confirmation, the bridge puts the paused
call in front of a person as a CHAP review, waits for their decision, made
at a CHAP desk, and returns the `ToolConfirmation` that resumes the run
with what they decided: the call as proposed, with their arguments, with
their answer to the tool's own question, or nothing. Every step is a
hash-linked entry on the workspace's audit log.

The paused call, its tool name, arguments and id, is the artefact under
review. An edit is an RFC 6902 patch on the log, so the chain records what
changed and why.

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

ADK's tool confirmation is experimental, and ADK does not support it with
`DatabaseSessionService` or `VertexAiSessionService`: run it with a session
service that does, such as `InMemorySessionService`. Where the app uses
ADK's resume feature, send the confirmation with the invocation id of the
run that asked for it.

## Quick start

ADK pauses a call that needs confirmation with an
`adk_request_confirmation` call. `request_confirmation` opens a review of
it and waits for the person's decision; the run resumes with the
confirmation it returns. This runs offline: a scripted model proposes the
transfer, and a task on the event loop stands in for Alice at her desk.

```python
import asyncio

from google.genai import types
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool

from chap_coordinator import Coordinator
from chap_google_adk import ChapConfirmationBridge, apply_reviewed_args

coord = Coordinator(enable_chain=True)
bridge = ChapConfirmationBridge(coord, workspace="wsp_payments",
                                agent="agent:assistant#v1", reviewer="human:alice@example.org")


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


async def alice_at_her_desk() -> None:
    """What a CHAP desk does for Alice: it shows her the call put to her,
    and she decides it in her own name. Here she caps the amount."""
    def call(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": {
            "workspace": "wsp_payments", "from": "human:alice@example.org", **params}})

    while not (submitted := call("audit.read", filter={"method": "task.complete"})["result"]["entries"]):
        await asyncio.sleep(0.1)
    review = submitted[0]["envelope"]["params"]
    print("Alice is shown:", review["output"])
    call("decide.override", task_id=review["task_id"], rationale="over the desk limit",
         diff=[{"op": "replace", "path": "/args/amount", "value": 50}])


async def main() -> None:
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

    desk = asyncio.create_task(alice_at_her_desk())
    confirmation = await bridge.request_confirmation(paused, llm_agent=agent)
    await desk
    async for event in runner.run_async(user_id="u", session_id="s", new_message=types.Content(
            role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=paused.id, name="adk_request_confirmation",
                response=confirmation.model_dump()))])):
        for part in (event.content.parts if event.content else []):
            if part.function_response:
                print(part.function_response.response)


asyncio.run(main())
```

## What the person's decision does

```
CHAP decision                         ToolConfirmation
---------------------------------     -----------------------------------
decide.approve                        confirmed=True
decide.override of the arguments      confirmed=True, payload={"args": ...}
decide.override of a tool's payload   confirmed=True, payload=the person's
anything else, or no decision         confirmed=False
```

The review is on a task that requires review, so only a person's decision
completes it. The run carries out exactly what was decided, or nothing:

- **An edit of the arguments.** ADK runs a confirmed call with the
  arguments the model proposed. The person's arguments travel in the
  confirmation's payload, and `apply_reviewed_args`, the agent's
  `before_tool_callback`, runs the call with them. The bridge confirms an
  edit only where it can see it run: `llm_agent`, the agent whose tool the
  call names, has that callback, and the tool takes the edited arguments,
  with none missing that it needs and none it does not take, which ADK
  would refuse or drop. Otherwise the call is not confirmed, and the hint
  says why.
- **A tool's own question.** A tool that calls
  `tool_context.request_confirmation(hint=..., payload=...)`, as a leave
  tool asks for `approved_days`, has its question and payload reviewed
  with the call, where the bridge is given the `adk_request_confirmation`
  call itself; the tool reads the person's version of the payload. Its
  arguments run as the model wrote them, so an edit of them is not
  confirmed.
- **Anything else** is not confirmed, with the person's note as the hint:
  a rejection, an abstention, an edit to the call itself, or a revision,
  whose task is then declined, as the model's next call is reviewed on a
  task of its own.

## Waiting, timeouts and cancellation

The bridge reads the task every two seconds (`poll_seconds`) for as long
as the reviewer takes, or until `timeout` seconds have passed, and never
holds up the event loop. A call nobody decided in time is not confirmed,
and its review is withdrawn, so no decision lands later on a call that
never ran. Cancelling the awaiting task withdraws the review too.

## At a desk, over HTTP

A desk such as `chap-coordinator serve`, or a project from
`create-chap-app --template support-desk`, keeps the coordinator in its own
process. The bridge reaches it with `chap_coordinator.client`:

```python
import json

from chap_coordinator.client import HttpCoordinator, Participant
from chap_google_adk import ChapConfirmationBridge

config = json.load(open("chap.config.json"))
agent_uri = config["agent"]["uri"]
http = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
bridge = ChapConfirmationBridge(
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

Where people decide in your own interface, `record_decision(paused,
confirmation)` records the confirmation your code made, in the reviewer's
name, as what it will make ADK do: not confirmed is a rejection; confirmed
with `payload={"args": ...}` that differ from the call's is an override of
the arguments; confirmed with a tool's own payload is an override of the
payload it asked for, recorded as the tool will read it; confirmed
otherwise is an approval.

```python
confirmation = ToolConfirmation(confirmed=True, payload={"args": {"amount": 50, "to": "Alice"}})
bridge.record_decision(paused, confirmation, llm_agent=agent, rationale="over the desk limit")
```

An edit of the arguments is recorded only where ADK runs it, as above:
give `llm_agent`. One ADK would not run as recorded, and a `decision`,
`returned` or `diff` that disagrees with the confirmation, raise
`ValueError`, with nothing recorded. An override is a refining edit
(`intent_preserved=true`) by default; set it `false` for a substituting
edit.

It writes the agent's `task.create` and `task.complete`, then the
`decide.*` call in the approver's name: a person's `human:` URI, the
bridge's `reviewer` unless `approver` names another. The coordinator holds
that call and nothing more: no signature from the person, and no sign that
they saw anything but what your code says. So each recorded decision
carries `via: "bridge-record"`, which the log keeps and chap-analytics
reads, and `approved_artefact_digest`, the digest of the call. Where people
sign, nothing is recorded: `record_decision` raises `PersonRequired`, and
they decide at the desk through `request_confirmation`.

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

- Lead with the desk: `request_confirmation` opens the review and waits
  for the person's decision, as the quick start shows. `record_decision`
  is the recording mode above, refused where people sign.
- Give the agent `before_tool_callback=apply_reviewed_args`, and the
  bridge the agent as `llm_agent=`, for an edit of the arguments to run or
  be recorded. Under 0.3.4 an edit was recorded and ADK ran the model's
  arguments. Pass the `adk_request_confirmation` call itself, so a tool's
  own question and payload are reviewed with the call.
- A recorded decision opens its review on a task that requires one, with
  `task.create` then `task.complete`, and no `review.request`; it carries
  `via: "bridge-record"` and the call's digest. The approver must be a
  person, a `human:` URI.
- The bridge joins the agent when it is made. Where nobody signs, the
  reviewer joins when the first review opens; where people sign, the
  bridge joins no person.
- A refusal raises `chap_coordinator.client.ChapError`, exported here as
  `ChapError`; `ChapBridgeError` names the same class. Its message reads
  `<method>: <message> (<code>)`.

## Example

`examples/01-approve-edit-reject.py` drives confirmation-gated tools against
real `google-adk` (offline, no API key) through a desk's decisions, an
approval, two edits, a rejection and a leave tool's own payload, printing
what each call ran with, then records a confirmation made in the script's
own interface, and prints the chain. `tests/test_runner.py` runs
`request_confirmation` and `record_decision` with a real runner, against an
in-process desk and a desk server where people sign.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `google-adk` 1.29 and later (optional; tested with google-adk 2.11.0 and
  google-genai 2.33.0)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
