"""
chap-langgraph in a real LangGraph graph: a checkpointer, interrupt() and
Command(resume=...), and LangGraph's fake chat model drafting the reply.

LangGraph runs a node again from its first line on resume, so these tests
count what runs twice. They are skipped where LangGraph is not installed;
scripts/test-bridges.sh installs it.
"""

import sys
from pathlib import Path
from typing import Any, Optional, TypedDict

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

pytest.importorskip("langgraph")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_langgraph import ChapBridge, ChapBridgeError, ReviewRejected, hil_review


REVIEWER = "human:alice@example.org"
SHOWN = "We will refund you 100 EUR today."
REDRAFTED = "We will refund you 1000 EUR today."


class State(TypedDict, total=False):
    ticket: str
    reply: Any
    rejected: Optional[str]
    chap_task_id: str
    chap_artefact: Any
    chap_artefact_digest: str
    chap_kind: str


class Harness:
    """A two-node graph built as the README shows, and what it did."""

    def __init__(self, catch_rejection: bool = True, desk: bool = False) -> None:
        self.coord = Coordinator(CoordinatorOptions(enable_chain=True))
        self.bridge = ChapBridge(self.coord, workspace="wsp_graph",
                                 agent="agent:drafter#v1", reviewer=REVIEWER)
        # A second call to the model would draft a different reply.
        self.model = GenericFakeChatModel(messages=iter([
            AIMessage(content=SHOWN), AIMessage(content=REDRAFTED)]))
        self.drafts = 0
        self.sent: list = []
        bridge = self.bridge

        def draft(state: State) -> State:
            self.drafts += 1
            reply = {"reply": self.model.invoke(state["ticket"]).content}
            return hil_review(bridge, reply, kind="draft_reply")

        def review(state: State) -> State:
            decision = interrupt({"task_id": state["chap_task_id"],
                                  "draft": state["chap_artefact"],
                                  "digest": state["chap_artefact_digest"]})
            if desk:
                decision = bridge.wait_for_decision(state["chap_task_id"], timeout=5,
                                                    poll_seconds=0.01)
            if not catch_rejection:
                return {"reply": bridge.apply_decision(
                    state["chap_task_id"], decision, state["chap_artefact"])}
            try:
                reply = bridge.apply_decision(state["chap_task_id"], decision,
                                              state["chap_artefact"])
            except ReviewRejected as rejected:
                return {"reply": None, "rejected": rejected.rationale}
            return {"reply": reply}

        def send(state: State) -> State:
            if state.get("reply") is not None:
                self.sent.append(state["reply"])
            return {}

        graph = StateGraph(State)
        graph.add_node("draft", draft)
        graph.add_node("review", review)
        graph.add_node("send", send)
        graph.add_edge(START, "draft")
        graph.add_edge("draft", "review")
        graph.add_edge("review", "send")
        graph.add_edge("send", END)
        self.graph = graph.compile(checkpointer=InMemorySaver())
        self.config = {"configurable": {"thread_id": "ticket-1"}}

    def pause(self) -> dict:
        out = self.graph.invoke({"ticket": "Where is my refund?"}, self.config)
        return out["__interrupt__"][0].value

    def resume(self, decision: Any) -> dict:
        return self.graph.invoke(Command(resume=decision), self.config)

    def calls(self, method: str) -> list[dict]:
        return [e["envelope"]["params"] for e in self.bridge.audit()
                if "envelope" in e and e["envelope"]["method"] == method]

    def task(self, task_id: str):
        return self.coord.workspaces["wsp_graph"].tasks[task_id]


def test_an_approval_lands_on_the_draft_the_reviewer_saw() -> None:
    h = Harness()
    shown = h.pause()
    assert shown["draft"] == {"reply": SHOWN}

    final = h.resume("approve")

    # Resuming ran the review node again and the draft node not at all: one
    # draft, one task, and the approval on the task the reviewer was shown.
    assert h.drafts == 1
    assert len(h.calls("task.create")) == 1
    assert [p["task_id"] for p in h.calls("decide.approve")] == [shown["task_id"]]
    assert h.calls("decide.approve")[0]["approved_artefact_digest"] == shown["digest"]
    assert final["reply"] == {"reply": SHOWN}
    assert h.sent == [{"reply": SHOWN}]
    task = h.task(shown["task_id"])
    assert (task.state, task.output) == ("completed", {"reply": SHOWN})


def test_an_override_is_what_carries_on() -> None:
    h = Harness()
    shown = h.pause()
    final = h.resume({
        "diff": [{"op": "replace", "path": "/reply",
                  "value": "We will refund you 50 EUR today."}],
        "rationale": "policy cap",
    })
    assert final["reply"] == {"reply": "We will refund you 50 EUR today."}
    assert h.sent == [{"reply": "We will refund you 50 EUR today."}]
    assert h.task(shown["task_id"]).output == {"reply": "We will refund you 50 EUR today."}


def test_a_rejected_draft_does_not_carry_on() -> None:
    h = Harness()
    shown = h.pause()
    final = h.resume({"action": "reject", "rationale": "wrong amount"})
    assert final["reply"] is None
    assert final["rejected"] == "wrong amount"
    assert h.sent == []
    assert h.task(shown["task_id"]).state == "declined"


def test_an_uncaught_rejection_stops_the_run() -> None:
    h = Harness(catch_rejection=False)
    h.pause()
    with pytest.raises(ReviewRejected):
        h.resume("reject")
    assert h.sent == []


def test_a_decision_naming_another_draft_is_refused() -> None:
    """The interface sends back the digest of what it showed. A digest that
    names another draft is refused, and the task stays under review."""
    h = Harness()
    shown = h.pause()
    with pytest.raises(ChapBridgeError) as raised:
        h.resume({"action": "approve", "approved_artefact_digest": "sha256:" + "0" * 64})
    assert raised.value.code == -32074
    assert h.task(shown["task_id"]).state == "review_requested"
    assert h.sent == []


def test_a_decision_made_at_a_desk_is_read_and_not_recorded_again() -> None:
    h = Harness(desk=True)
    shown = h.pause()
    # The person decides at a desk that shares the coordinator.
    h.coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                      "params": {"workspace": "wsp_graph", "from": REVIEWER,
                                 "type": "human"}})
    out = h.coord.dispatch({"jsonrpc": "2.0", "id": "d", "method": "decide.override",
                            "params": {"workspace": "wsp_graph", "from": REVIEWER,
                                       "task_id": shown["task_id"],
                                       "diff": [{"op": "replace", "path": "/reply",
                                                 "value": "50 EUR today."}],
                                       "rationale": "policy cap"}})
    assert out["result"]["state"] == "completed"

    # The resume only wakes the graph; the decision is read from CHAP.
    final = h.resume(True)

    assert final["reply"] == {"reply": "50 EUR today."}
    assert h.sent == [{"reply": "50 EUR today."}]
    assert len(h.calls("decide.override")) == 1
    assert h.drafts == 1
