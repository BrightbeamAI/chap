"""
chap-ag2 in a real AG2 0.x conversation: the assistant answers with no LLM,
a UserProxyAgent's get_human_input opens a review through the bridge and
waits, a person decides at a desk sharing the coordinator, and the reply
the bridge returns is what AG2 carries on with.

Skipped where AG2's 0.x `autogen` package is not installed;
scripts/test-bridges.sh installs it, and fails where they are skipped.
"""

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

autogen = pytest.importorskip("autogen")

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_ag2 import ChapTurnBridge, ReviewRejected

WS = "wsp_ag2"
REVIEWER = "human:alice@example.org"
DRAFT = "Draft: refund $100 to Alice."


class Desk(threading.Thread):
    """A person at a desk sharing the coordinator: they decide the first
    review that opens, in their own name, and keep what they were shown."""

    def __init__(self, coord, method, delay=0.0, **params):
        super().__init__(daemon=True)
        self.coord, self.method, self.delay, self.params = coord, method, delay, params
        self.shown = self.answer = None

    def call(self, method, **params):
        return self.coord.dispatch({"jsonrpc": "2.0", "id": f"desk-{method}", "method": method,
                                    "params": {"workspace": WS, "from": REVIEWER, **params}})

    def run(self):
        for _ in range(500):
            submitted = self.call("audit.read", filter={"method": "task.complete"})["result"]["entries"]
            if submitted:
                review = submitted[0]["envelope"]["params"]
                self.shown = review["output"]
                time.sleep(self.delay)
                self.answer = self.call(self.method, task_id=review["task_id"], **self.params)
                return
            time.sleep(0.01)


def bridge_and_coord():
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    return coord, ChapTurnBridge(coord, workspace=WS, agent="agent:assistant#v1", reviewer=REVIEWER)


def chat(method, timeout=5, delay=0.0, **params):
    coord, bridge = bridge_and_coord()

    class ReviewedUser(autogen.UserProxyAgent):
        def get_human_input(self, prompt, **kw):
            return bridge.request_reply(self.last_message(), timeout=timeout, poll_seconds=0.01)

    assistant = autogen.ConversableAgent("assistant", llm_config=False, default_auto_reply=DRAFT)
    user = ReviewedUser("user", human_input_mode="ALWAYS", code_execution_config=False, default_auto_reply="")
    desk = Desk(coord, method, delay, **params)
    desk.start()
    try:
        result = user.initiate_chat(assistant, message="please draft a refund", max_turns=2, silent=True)
    finally:
        desk.join()
    return [m["content"] for m in result.chat_history], bridge, coord, desk


def test_an_approval_lets_the_chat_carry_on():
    history, bridge, _, _ = chat("decide.approve")
    assert history == ["please draft a refund", DRAFT, "", DRAFT]
    decided = [e["envelope"]["params"]["from"] for e in bridge.audit()
               if "envelope" in e and e["envelope"]["method"].startswith("decide.")]
    assert decided == [REVIEWER]


def test_an_override_sends_the_reviewers_version():
    history, _, _, _ = chat("decide.override", rationale="cap",
                            diff=[{"op": "replace", "path": "", "value": "Draft: refund $50 to Alice."}])
    assert history[2] == "Draft: refund $50 to Alice."


def test_a_revision_sends_the_note_and_closes_the_task():
    history, _, coord, _ = chat("decide.reject", comment="shorter, please", request_revision=True)
    assert history[2] == "shorter, please"
    [task] = coord.workspaces[WS].tasks.values()
    assert task.state == "declined"


def test_a_rejection_ends_the_chat():
    with pytest.raises(ReviewRejected) as raised:
        chat("decide.reject", comment="no refunds this week")
    assert (raised.value.outcome, raised.value.rationale) == ("reject", "no refunds this week")


def test_a_wait_that_times_out_withdraws_the_review():
    with pytest.raises(TimeoutError, match="withdrawn"):
        chat("decide.approve", timeout=0.2, delay=0.5)


def tool_call_chat(method, **params):
    """The assistant proposes a transfer as a tool call; the person reviews it."""
    coord, bridge = bridge_and_coord()
    ran = []

    def transfer(amount: int, to: str) -> str:
        ran.append({"amount": amount, "to": to})
        return f"sent {amount} to {to}"

    class ReviewedUser(autogen.UserProxyAgent):
        def get_human_input(self, prompt, **kw):
            return bridge.request_reply(self.last_message(), timeout=5, poll_seconds=0.01)

    assistant = autogen.ConversableAgent("assistant", llm_config=False)
    proposals = iter([{"content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {
        "name": "transfer", "arguments": json.dumps({"amount": 10000, "to": "Mallory"})}}]}])

    def propose(recipient, messages=None, sender=None, config=None):
        return True, next(proposals, "TERMINATE")

    assistant.register_reply([autogen.Agent, None], propose)
    user = ReviewedUser("user", human_input_mode="ALWAYS", code_execution_config=False,
                        is_termination_msg=lambda m: (m.get("content") or "") == "TERMINATE")
    user.register_for_execution(name="transfer")(transfer)
    desk = Desk(coord, method, **params)
    desk.start()
    user.initiate_chat(assistant, message="pay the invoice", max_turns=2, silent=True)
    desk.join()
    return ran, desk


def test_a_tool_call_is_put_in_front_of_the_person_whole_and_runs_once_approved():
    ran, desk = tool_call_chat("decide.approve")
    assert desk.shown == {"content": None, "tool_calls": [
        {"id": "call_1", "name": "transfer", "arguments": {"amount": 10000, "to": "Mallory"}}]}
    assert ran == [{"amount": 10000, "to": "Mallory"}]


def test_an_edited_tool_call_is_not_run():
    ran, desk = tool_call_chat("decide.override", rationale="wrong payee", diff=[
        {"op": "replace", "path": "/tool_calls/0/arguments/to", "value": "Alice"}])
    assert "error" not in desk.answer
    assert ran == []


def test_an_async_chat_awaits_the_decision():
    coord, bridge = bridge_and_coord()

    class ReviewedUser(autogen.UserProxyAgent):
        async def a_get_human_input(self, prompt, **kw):
            return await bridge.request_reply_async(self.last_message(), timeout=5, poll_seconds=0.01)

    assistant = autogen.ConversableAgent("assistant", llm_config=False, default_auto_reply=DRAFT)
    user = ReviewedUser("user", human_input_mode="ALWAYS", code_execution_config=False, default_auto_reply="")
    desk = Desk(coord, "decide.override", rationale="cap",
                diff=[{"op": "replace", "path": "", "value": "Draft: refund $50 to Alice."}])
    desk.start()
    result = asyncio.run(user.a_initiate_chat(assistant, message="please draft a refund", max_turns=2, silent=True))
    desk.join()
    assert [m["content"] for m in result.chat_history][2] == "Draft: refund $50 to Alice."
