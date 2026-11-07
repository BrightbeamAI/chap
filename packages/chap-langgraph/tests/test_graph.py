"""
chap-langgraph in a real LangGraph graph: a checkpointer, interrupt() and
Command(resume=...), and LangGraph's fake chat model drafting the reply.

LangGraph runs a node again from its first line on resume, after a failure
and on a retry, so these tests count what runs twice. They are skipped
where LangGraph is not installed; scripts/test-bridges.sh installs it, and
fails where they are skipped.
"""

import operator
import sys
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Optional, TypedDict

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

pytest.importorskip("langgraph")

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy, Send, interrupt

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_langgraph import ChapBridge, ChapError, ReviewRejected, hil_review


WS = "wsp_graph"
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


def desk(coord: Coordinator, method: str, task_id: str, **params) -> dict:
    """A decision made at a desk that shares the coordinator."""
    out = coord.dispatch({"jsonrpc": "2.0", "id": "desk", "method": method,
                          "params": {"workspace": WS, "from": REVIEWER, "task_id": task_id, **params}})
    assert "error" not in out, out
    return out["result"]


class Harness:
    """The README's graph, draft then review then send, and what it did.

    With ``desk`` the person decides at a desk and the resume only wakes
    the graph; without it the resume value is the decision, made in an
    interface of the application's own. ``fail_once`` makes the review node
    fail once, after the decision is recorded and before it returns."""

    def __init__(self, desk: bool = False, catch_rejection: bool = True, fail_once: bool = False,
                 retry: Optional[RetryPolicy] = None) -> None:
        self.coord = Coordinator(CoordinatorOptions(enable_chain=True))
        self.bridge = ChapBridge(self.coord, workspace=WS, agent="agent:drafter#v1", reviewer=REVIEWER)
        # A second call to the model would draft a different reply.
        self.model = GenericFakeChatModel(messages=iter([AIMessage(content=SHOWN), AIMessage(content=REDRAFTED)]))
        self.drafts = 0
        self.reviews = 0
        self.sent: list = []
        self.failing = fail_once
        bridge = self.bridge

        def draft(state: State) -> State:
            self.drafts += 1
            reply = {"reply": self.model.invoke(state["ticket"]).content}
            return hil_review(bridge, reply, kind="draft_reply", input_={"ticket": state["ticket"]})

        def review(state: State) -> State:
            self.reviews += 1
            decision = interrupt({"task_id": state["chap_task_id"], "draft": state["chap_artefact"],
                                  "digest": state["chap_artefact_digest"]})
            if desk:
                decision = bridge.wait_for_decision(state["chap_task_id"], timeout=5, poll_seconds=0.01)
            try:
                reply = bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"])
            except ReviewRejected as rejected:
                if not catch_rejection:
                    raise
                return {"reply": None, "rejected": rejected.rationale}
            if self.failing:
                self.failing = False
                raise ConnectionError("the connection dropped after the decision was recorded")
            return {"reply": reply}

        def send(state: State) -> State:
            if state.get("reply") is not None:
                self.sent.append(state["reply"])
            return {}

        graph = StateGraph(State)
        graph.add_node("draft", draft)
        graph.add_node("review", review, retry_policy=retry)
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
        return self.coord.workspaces[WS].tasks[task_id]


# ---- the person decides at a desk ---------------------------------------


def test_a_decision_made_at_the_desk_is_what_the_graph_carries_on_with() -> None:
    h = Harness(desk=True)
    shown = h.pause()
    desk(h.coord, "decide.override", shown["task_id"], rationale="policy cap",
         diff=[{"op": "replace", "path": "/reply", "value": "We will refund you 50 EUR today."}])

    # The resume only wakes the graph; the decision is read from CHAP.
    final = h.resume(True)

    assert final["reply"] == {"reply": "We will refund you 50 EUR today."}
    assert h.sent == [{"reply": "We will refund you 50 EUR today."}]
    assert (h.drafts, len(h.calls("task.create")), len(h.calls("decide.override"))) == (1, 1, 1)


def test_a_resume_value_cannot_change_what_the_desk_decided() -> None:
    h = Harness(desk=True)
    shown = h.pause()
    desk(h.coord, "decide.reject", shown["task_id"], comment="wrong amount")
    final = h.resume("approve")
    assert (final["reply"], final["rejected"], h.sent) == (None, "wrong amount", [])
    assert h.calls("decide.approve") == []


def test_a_graph_that_waits_in_the_node_carries_on_once_the_person_decides() -> None:
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapBridge(coord, workspace=WS, agent="agent:drafter#v1", reviewer=REVIEWER)

    def draft(state: State) -> State:
        return hil_review(bridge, {"reply": SHOWN}, kind="draft_reply")

    def review(state: State) -> State:
        decision = bridge.wait_for_decision(state["chap_task_id"], timeout=5, poll_seconds=0.01)
        return {"reply": bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"])}

    def person() -> None:
        while not coord.workspaces[WS].tasks:
            time.sleep(0.01)
        [task_id] = list(coord.workspaces[WS].tasks)
        desk(coord, "decide.approve", task_id, comment="fine")

    graph = StateGraph(State)
    graph.add_node("draft", draft)
    graph.add_node("review", review)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "review")
    graph.add_edge("review", END)
    threading.Thread(target=person, daemon=True).start()
    final = graph.compile().invoke({"ticket": "t"})
    assert final["reply"] == {"reply": SHOWN}


# ---- the decision comes back in the resume value -------------------------


def test_an_approval_lands_on_the_draft_the_reviewer_saw() -> None:
    h = Harness()
    shown = h.pause()
    assert shown["draft"] == {"reply": SHOWN}

    final = h.resume("approve")

    # Resuming ran the review node again and the draft node not at all: one
    # draft, one task, and the approval on the task the reviewer was shown.
    assert (h.drafts, h.reviews) == (1, 2)
    assert len(h.calls("task.create")) == 1
    [approval] = h.calls("decide.approve")
    assert (approval["task_id"], approval["approved_artefact_digest"], approval["via"]) == (
        shown["task_id"], shown["digest"], "bridge-record")
    assert final["reply"] == {"reply": SHOWN} and h.sent == [{"reply": SHOWN}]
    task = h.task(shown["task_id"])
    assert (task.state, task.output) == ("completed", {"reply": SHOWN})


def test_an_override_is_what_carries_on() -> None:
    h = Harness()
    shown = h.pause()
    final = h.resume({"diff": [{"op": "replace", "path": "/reply", "value": "We will refund you 50 EUR today."}],
                      "rationale": "policy cap"})
    assert final["reply"] == {"reply": "We will refund you 50 EUR today."}
    assert h.sent == [{"reply": "We will refund you 50 EUR today."}]
    assert h.task(shown["task_id"]).output == {"reply": "We will refund you 50 EUR today."}


def test_a_rejected_draft_does_not_carry_on() -> None:
    h = Harness()
    shown = h.pause()
    final = h.resume({"action": "reject", "rationale": "wrong amount"})
    assert (final["reply"], final["rejected"], h.sent) == (None, "wrong amount", [])
    assert h.task(shown["task_id"]).state == "declined"


def test_an_uncaught_rejection_stops_the_run() -> None:
    h = Harness(catch_rejection=False)
    h.pause()
    with pytest.raises(ReviewRejected):
        h.resume("reject")
    assert h.sent == []


def test_a_rejection_is_not_retried_by_a_retry_policy() -> None:
    h = Harness(catch_rejection=False, retry=RetryPolicy(max_attempts=3, initial_interval=0.01))
    h.pause()
    with pytest.raises(ReviewRejected):
        h.resume({"action": "reject", "rationale": "no"})
    # The node ran once to pause and once on resume; LangGraph's default
    # retry policy does not retry a ReviewRejected.
    assert h.reviews == 2
    assert len(h.calls("decide.reject")) == 1


def test_a_review_node_that_fails_after_recording_finishes_when_run_again() -> None:
    h = Harness(fail_once=True)
    shown = h.pause()
    with pytest.raises(ConnectionError):
        h.resume("approve")
    # Run again, the node reads the approval back from the chain.
    final = h.graph.invoke(None, h.config)
    assert final["reply"] == {"reply": SHOWN} and h.sent == [{"reply": SHOWN}]
    assert len(h.calls("decide.approve")) == 1
    assert all("request" not in e for e in h.bridge.audit())   # no refusal on the log either


def test_the_draft_shown_carries_on_when_the_state_changed_before_the_resume() -> None:
    """The interface sends back the digest of what it showed, as the README
    advises, and a node or reducer changed the draft in the graph's state
    before the resume: the graph carries on with the draft approved."""
    h = Harness()
    shown = h.pause()
    h.graph.update_state(h.config, {"chap_artefact": {"reply": REDRAFTED}})
    final = h.resume({"action": "approve", "approved_artefact_digest": shown["digest"]})
    assert final["reply"] == {"reply": SHOWN} and h.sent == [{"reply": SHOWN}]


def test_a_decision_naming_another_draft_is_refused() -> None:
    h = Harness()
    shown = h.pause()
    with pytest.raises(ChapError) as raised:
        h.resume({"action": "approve", "approved_artefact_digest": "sha256:" + "0" * 64})
    assert raised.value.code == -32074
    assert h.task(shown["task_id"]).state == "review_requested"
    assert h.sent == []


# ---- several drafts at once ---------------------------------------------


class Parallel(TypedDict, total=False):
    refund_task_id: str
    refund_artefact: Any
    refund_artefact_digest: str
    refund_kind: str
    apology_task_id: str
    apology_artefact: Any
    apology_artefact_digest: str
    apology_kind: str
    sent: Annotated[list, operator.add]


def test_drafts_in_parallel_branches_each_open_one_review() -> None:
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapBridge(coord, workspace=WS, agent="agent:drafter#v1", reviewer=REVIEWER)

    def refund(state):
        return hil_review(bridge, {"reply": "refund 10"}, kind="refund", prefix="refund_")

    def apology(state):
        return hil_review(bridge, {"reply": "sorry"}, kind="apology", prefix="apology_")

    def review(state):
        out = []
        for key in ("refund_", "apology_"):
            decision = bridge.wait_for_decision(state[f"{key}task_id"], timeout=5, poll_seconds=0.01)
            out.append(bridge.apply_decision(state[f"{key}task_id"], decision, state[f"{key}artefact"]))
        return {"sent": out}

    def person():
        while len(coord.workspaces[WS].tasks) < 2:
            time.sleep(0.01)
        for task_id in list(coord.workspaces[WS].tasks):
            desk(coord, "decide.approve", task_id)

    graph = StateGraph(Parallel)
    graph.add_node("refund", refund)
    graph.add_node("apology", apology)
    graph.add_node("review", review)
    graph.add_edge(START, "refund")
    graph.add_edge(START, "apology")
    graph.add_edge(["refund", "apology"], "review")
    graph.add_edge("review", END)
    threading.Thread(target=person, daemon=True).start()
    final = graph.compile().invoke({})
    assert final["sent"] == [{"reply": "refund 10"}, {"reply": "sorry"}]
    assert [t.state for t in coord.workspaces[WS].tasks.values()] == ["completed", "completed"]


class Tickets(TypedDict, total=False):
    tickets: list
    opened: Annotated[list, operator.add]
    sent: Annotated[list, operator.add]


def test_a_review_for_each_item_sent_out() -> None:
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapBridge(coord, workspace=WS, agent="agent:drafter#v1", reviewer=REVIEWER)

    def fan_out(state):
        return [Send("draft_one", {"ticket": t}) for t in state["tickets"]]

    def draft_one(state):
        return {"opened": [hil_review(bridge, {"reply": f"about {state['ticket']}"}, kind="draft_reply")]}

    def review(state):
        sent = []
        for opened in state["opened"]:
            decision = bridge.wait_for_decision(opened["chap_task_id"], timeout=5, poll_seconds=0.01)
            try:
                sent.append(bridge.apply_decision(opened["chap_task_id"], decision, opened["chap_artefact"]))
            except ReviewRejected:
                pass
        return {"sent": sent}

    def person():
        while len(coord.workspaces[WS].tasks) < 2:
            time.sleep(0.01)
        for task in list(coord.workspaces[WS].tasks.values()):
            if task.pending_artefact == {"reply": "about a"}:
                desk(coord, "decide.approve", task.id)
            else:
                desk(coord, "decide.reject", task.id, comment="no")

    graph = StateGraph(Tickets)
    graph.add_node("draft_one", draft_one)
    graph.add_node("review", review)
    graph.add_conditional_edges(START, fan_out, ["draft_one"])
    graph.add_edge("draft_one", "review")
    graph.add_edge("review", END)
    threading.Thread(target=person, daemon=True).start()
    final = graph.compile().invoke({"tickets": ["a", "b"]})
    assert final["sent"] == [{"reply": "about a"}]
