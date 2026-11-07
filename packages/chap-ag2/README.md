# chap-ag2

Bridge between [AG2](https://github.com/ag2ai/ag2) (AutoGen) and the CHAP
Coordinator. At an AG2 human-input turn, the bridge puts the agent's
message, its text and the tool calls it would make, in front of a person as
a CHAP review, waits for their decision, made at a CHAP desk, and gives AG2
the reply that carries it out. With AG2 1.x, a tool middleware puts each
call of a tool in front of a person the same way. Every step is a
hash-linked entry on the workspace's audit log.

## Install

```bash
pip install chap-ag2
```

Depends on `chap-coordinator>=0.3.4`. AG2 is optional: the bridge reads
messages and replies as plain values. Install the extra to run a live
conversation:

```bash
pip install "chap-ag2[ag2]"
```

The extra installs AG2 0.x, whose `autogen` package asks for human input
through `get_human_input`, where `request_reply` works; it asks for
`ag2>=0.9,<1`. AG2 1.0 replaced that package: it asks a person through its
own hook (`ag2.hitl`), and its `approval_required()` tool middleware asks a
yes or no through that hook. With AG2 1.x, install it yourself and gate
tools with `tool_approval`, below:

```bash
pip install chap-ag2 "ag2>=1.2,<2"
```

## Quick start

The message under review and the reply meet inside `get_human_input`:
`self.last_message()` is the agent's message, and the return value is the
human's reply. `request_reply` opens a review of the message and waits for
the person's decision. This runs offline: the assistant answers with a
fixed draft, and a thread stands in for Alice at her desk.

```python
import threading
import time

from autogen import ConversableAgent, UserProxyAgent

from chap_coordinator import Coordinator
from chap_ag2 import ChapTurnBridge

coord = Coordinator(enable_chain=True)
bridge = ChapTurnBridge(coord, workspace="wsp_support",
                        agent="agent:assistant#v1", reviewer="human:alice@example.org")


class ReviewedUser(UserProxyAgent):
    def get_human_input(self, prompt, **kw):
        return bridge.request_reply(self.last_message())


def alice_at_her_desk() -> None:
    """What a CHAP desk does for Alice: it shows her the message put to her,
    and she decides it in her own name. Here she lowers the refund."""
    def call(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": {
            "workspace": "wsp_support", "from": "human:alice@example.org", **params}})

    while not (submitted := call("audit.read", filter={"method": "task.complete"})["result"]["entries"]):
        time.sleep(0.1)
    review = submitted[0]["envelope"]["params"]
    print("Alice is shown:", review["output"])
    call("decide.override", task_id=review["task_id"], rationale="over the desk limit",
         diff=[{"op": "replace", "path": "", "value": "Draft: refund $50 to Alice."}])


threading.Thread(target=alice_at_her_desk).start()
assistant = ConversableAgent("assistant", llm_config=False,
                             default_auto_reply="Draft: refund $100 to Alice.")
user = ReviewedUser("user", human_input_mode="ALWAYS", code_execution_config=False)
result = user.initiate_chat(assistant, message="please draft a refund", max_turns=2, silent=True)
print([message["content"] for message in result.chat_history])
```

## What the person's decision does

The message is reviewed whole: its text, and every tool or function call
it would make, with their arguments. A message with neither is refused with
`ValueError`, since nobody can approve what they were not shown. The review
is on a task that requires review, so only a person's decision completes
it. The reply carries the decision out:

```
CHAP decision                          reply to AG2
-----------------------------------    ---------------------------------
decide.approve                         "" (AG2 carries on: it runs the
                                       message's tool calls as proposed)
decide.override of a text message      the person's version
decide.override of a tool call         a reply that refuses the call and
                                       gives the person's version of it
decide.reject, asking for another      the person's note
any other ending                       raises ReviewRejected
```

AG2 0.x runs a tool call only with the arguments the model wrote, and a
non-empty human reply stops the calls of the message it answers. So an
edit of a call is not run: the reply gives the agent the person's version,
to make again, and that call is reviewed in its turn. A revision request
sends the person's note, and its task is declined: the agent's next
message is reviewed on a task of its own. A rejection raises
`ReviewRejected` from `get_human_input`, which ends the chat, so nothing
takes its last message for an approved one; its `outcome` says how the
review ended.

## What a chat does with it

An override of a text message reaches the agent as the human's reply: the
chat carries the person's version as feedback, and the agent's next message
is a message of its own, which is reviewed at the next human turn. A chat
that ends on `max_turns` ends on a message nobody was asked about, since
AG2 asks for no human input then: end the chat on the person's approval
with `is_termination_msg`, or take what was approved from CHAP, where the
task's output is the message as decided.

In a group chat the person's agent talks to the `GroupChatManager`, and
`self.last_message()` is the group's last message, whoever wrote it. The
bridge records it as the work of its `agent`, so review in a group only
where that agent spoke last, by its `name`.

## Waiting, timeouts and async chats

The bridge reads the task every two seconds (`poll_seconds`) for as long
as the reviewer takes, or until `timeout` seconds have passed. It then
withdraws the review, so no decision lands later on a message the chat did
not wait for, and raises `TimeoutError`, which ends the chat.

A chat started with `a_initiate_chat` asks `a_get_human_input`; await
`request_reply_async` there. It never holds up the event loop, and
cancelling the awaiting task withdraws the review:

```python
class ReviewedUser(UserProxyAgent):
    async def a_get_human_input(self, prompt, **kw):
        return await bridge.request_reply_async(self.last_message())
```

## AG2 1.x: approve a tool call

AG2 1.x gates a tool with middleware. `bridge.tool_approval()` is a tool
middleware of the same kind as AG2's own `approval_required()`: it puts
each call of the tool in front of a person at a CHAP desk, where they can
also change its arguments, and runs it as they decide:

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

The call, its tool name, arguments and call id, is the artefact under
review, on a task that requires review. An approval runs the call as the
model made it, and an override of its arguments runs it with the person's.
Anything else runs nothing and answers the model with the person's note: a
rejection, an abstention, an edit to anything but the arguments, or a
revision, whose task is then declined. The wait never holds up the event
loop. A call nobody decided within `timeout` seconds runs nothing, the
model is told so, and the review is withdrawn; a cancelled turn withdraws
it too.

## At a desk, over HTTP

A desk such as `chap-coordinator serve`, or a project from
`create-chap-app --template support-desk`, keeps the coordinator in its own
process. The bridge reaches it with `chap_coordinator.client`:

```python
import json

from chap_coordinator.client import HttpCoordinator, Participant
from chap_ag2 import ChapTurnBridge

config = json.load(open("chap.config.json"))
agent_uri = config["agent"]["uri"]
http = HttpCoordinator(f"http://127.0.0.1:{config['port']}/chap")
bridge = ChapTurnBridge(
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

Where people decide in your own interface, `record_turn(message, reply,
decision=...)` records the turn your code took, in the reviewer's name:

```
decision      CHAP envelope
--------      ---------------------------
approve       decide.approve
override      decide.override   (message vs reply, as a diff)
reject        decide.reject
```

AG2's human turn is a weak signal: the same loop carries plain dialogue,
edits, approvals and "exit", and inferring intent would write decisions
nobody made. So `decision` says which it was, and the one inference is
that an empty reply approves: a non-empty reply with no `decision` records
nothing. An edit of a tool call is refused with `ValueError`, since AG2
0.x would not run it. An override is a refining edit
(`intent_preserved=true`) by default; set it `false` for a substituting
edit.

It writes the agent's `task.create` and `task.complete`, then the
`decide.*` call in the approver's name: a person's `human:` URI, the
bridge's `reviewer` unless `approver` names another. The coordinator holds
that call and nothing more: no signature from the person, and no sign
that they saw anything but what your code says. So each recorded decision
carries `via: "bridge-record"`, which the log keeps and chap-analytics
reads, and `approved_artefact_digest`, the digest of the message. Where
people sign, nothing is recorded: `record_turn` raises `PersonRequired`,
and they decide at the desk through `request_reply`.

## What you get in the audit chain

The quick start yields the following, every entry carrying `prev_hash`:

```
seq=0  workspace.create
seq=1  participant.join  agent:assistant#v1
seq=2  participant.join  human:alice@example.org
seq=3  task.create       agent:assistant#v1   kind=agent_message  review_required=true
seq=4  task.complete     agent:assistant#v1   the message; opens the review
seq=5  decide.override   human:alice@example.org  diff=[{op:replace, path:, value:"Draft: refund $50 to Alice."}]
```

The chain verifies externally, or anchors to a SCITT transparency service
with the `audit-scitt/1.0` profile.

## Upgrading from 0.3.4

- The `ag2` extra installs AG2 0.x (`ag2>=0.9,<1`). Under 0.3.4 it
  resolved to AG2 1.x, which has no `autogen` module.
- Lead with the desk: `request_reply` opens the review and waits for the
  person's decision, as the quick start shows. `record_turn` is the
  recording mode above, refused where people sign.
- A message is reviewed with its tool calls. Its content alone was
  reviewed before, which for a tool-call message was nothing, and an
  approval ran the call.
- A recorded turn opens its review on a task that requires one, with
  `task.create` then `task.complete`, and no `review.request`; it carries
  `via: "bridge-record"` and the message's digest. The approver must be a
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
  `ChapError`; `ChapBridgeError` names the same class. `ReviewRejected` is
  a `RuntimeError`, with an `outcome`.

## Example

`examples/01-approve-edit-reject.py` runs a real AG2 0.x conversation (no
LLM needed) whose messages Sam decides at a desk, an approval, an edit, a
revision and a rejection, then records a turn taken in the script's own
interface, and prints the chain. `tests/test_conversation.py` runs
`request_reply` in real conversations, tool calls included, and
`tests/test_tool_approval.py` runs `tool_approval` on an AG2 1.x agent with
a scripted model, each with a person deciding at a desk.

## Compatibility

- `chap-coordinator` 0.3.4 and later
- `ag2` 0.9 to 0.x for `request_reply` and `record_turn`, through
  `get_human_input` (optional; tested with ag2 0.9 and 0.14.0)
- `ag2` 1.2 and later 1.x for `tool_approval` (tested with ag2 1.2.0 and
  1.2.2)
- Python 3.10, 3.11, 3.12, 3.13

## License

Apache 2.0. See [LICENSE](./LICENSE).
