"""
chap-ag2 in a real AG2 0.x conversation: the assistant drafts with no LLM,
a UserProxyAgent's get_human_input opens a review through the bridge and
waits, a person decides at a desk sharing the coordinator, and the reply
the bridge returns is what AG2 carries on with.

Skipped where AG2's 0.x `autogen` package is not installed;
scripts/test-bridges.sh installs it.
"""

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


REVIEWER = "human:alice@example.org"
DRAFT = "Draft: refund $100 to Alice."


def desk(coord, method, **params):
    coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "wsp_ag2", "from": REVIEWER, "type": "human"}})
    for _ in range(500):
        open_ = [t for t in coord.workspaces["wsp_ag2"].tasks.values()
                 if t.state == "review_requested"]
        if open_:
            out = coord.dispatch({"jsonrpc": "2.0", "id": "d", "method": method,
                                  "params": {"workspace": "wsp_ag2", "from": REVIEWER,
                                             "task_id": open_[0].id, **params}})
            assert "error" not in out, out
            return
        time.sleep(0.01)
    raise AssertionError("no review reached the desk")


def chat(method, **params):
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapTurnBridge(coord, workspace="wsp_ag2", agent="agent:assistant#v1",
                            reviewer=REVIEWER)

    class ReviewedUser(autogen.UserProxyAgent):
        def get_human_input(self, prompt, **kw):
            return bridge.request_reply(self.last_message(), timeout=5, poll_seconds=0.01)

    assistant = autogen.ConversableAgent("assistant", llm_config=False,
                                         default_auto_reply=DRAFT)
    user = ReviewedUser("user", human_input_mode="ALWAYS", code_execution_config=False,
                        default_auto_reply="")
    person = threading.Thread(target=desk, args=(coord, method), kwargs=params)
    person.start()
    try:
        result = user.initiate_chat(assistant, message="please draft a refund",
                                    max_turns=2, silent=True)
    finally:
        person.join()
    return [m["content"] for m in result.chat_history], bridge


def test_an_approval_lets_the_chat_carry_on():
    history, bridge = chat("decide.approve")
    assert history == ["please draft a refund", DRAFT, "", DRAFT]
    decided = [e["envelope"]["params"]["from"] for e in bridge.audit()
               if e["envelope"]["method"].startswith("decide.")]
    assert decided == [REVIEWER]


def test_an_override_sends_the_reviewers_version():
    history, _ = chat("decide.override",
                      diff=[{"op": "replace", "path": "", "value": "refund $50 to Alice"}],
                      rationale="over the desk limit")
    assert history[2] == "refund $50 to Alice"


def test_a_request_for_a_revision_sends_the_note():
    history, _ = chat("decide.reject", comment="ask for the order number",
                      request_revision=True)
    assert history[2] == "ask for the order number"


def test_a_rejection_ends_the_chat():
    with pytest.raises(ReviewRejected) as raised:
        chat("decide.reject", comment="not on the allow-list")
    assert raised.value.rationale == "not on the allow-list"
