"""
Tests for chap-langgraph without LangGraph: the calls a graph's nodes make,
in the order a graph makes them.

    1. The draft node opens a review with hil_review(): task.create, then
       task.complete, on a task that requires review.
    2. The person decides at a desk, and wait_for_decision() reads it back;
       or the decision comes back in a resume value, and apply_decision()
       records it in the reviewer's name, marked as recorded.
    3. apply_decision() returns the draft as the coordinator holds it.

tests/test_graph.py runs the same bridge in a real LangGraph graph.
"""

import json
import sys
import threading
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions, content_hash
from chap_coordinator.client import ChapError, HttpCoordinator, Participant, Signer
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_coordinator.storage.sqlite import SqliteStore
from chap_langgraph import (
    ChapBridge, ChapBridgeError, Decision, PersonRequired, ReviewRejected, hil_review,
)


WS = "wsp_lg_test"
AGENT = "agent:drafter#v1"
REVIEWER = "human:alice@example.org"
ENROL = "TESTS-ENROL"


@pytest.fixture
def coord() -> Coordinator:
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, enable_chain=True,
    ))


@pytest.fixture
def bridge(coord: Coordinator) -> ChapBridge:
    return ChapBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)


def calls(bridge: ChapBridge, method: str) -> list[dict]:
    return [e["envelope"]["params"] for e in bridge.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


def desk_decides(coord: Coordinator, method: str, task_id: str, reviewer: str = REVIEWER,
                 **params) -> dict:
    """A decision made at a desk that shares the coordinator, in the
    person's own name."""
    out = coord.dispatch({"jsonrpc": "2.0", "id": "desk", "method": method,
                          "params": {"workspace": WS, "from": reviewer,
                                     "task_id": task_id, **params}})
    assert "error" not in out, out
    return out["result"]


def task_of(coord: Coordinator, task_id: str):
    return coord.workspaces[WS].tasks[task_id]


# ---- opening a review ---------------------------------------------------


def test_the_bridge_joins_the_agent_and_nobody_else(coord, bridge):
    assert set(coord.workspaces[WS].members) == {AGENT}
    again = ChapBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    assert again.workspace == bridge.workspace and set(coord.workspaces[WS].members) == {AGENT}


def test_hil_review_opens_the_review_with_the_drafts_completion(bridge, coord):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    state = hil_review(bridge, {"reply": "draft"}, kind="draft_response", input_={"ticket": "t1"})

    # review.request from the agent whose draft it is would be refused on a
    # task that requires review; the completion opens the review.
    assert [m for m in seen if m.startswith(("task.", "review."))] == ["task.create", "task.complete"]
    task = task_of(coord, state["chap_task_id"])
    assert (task.state, task.review_required, task.input) == ("review_requested", True, {"ticket": "t1"})
    assert task.review.requested_to == [REVIEWER]
    assert state == {"chap_task_id": task.id, "chap_artefact": {"reply": "draft"},
                     "chap_artefact_digest": content_hash({"reply": "draft"}),
                     "chap_kind": "draft_response"}


def test_drafts_in_parallel_branches_write_keys_of_their_own(bridge):
    refund = hil_review(bridge, {"reply": "refund"}, kind="refund", prefix="refund_")
    apology = hil_review(bridge, {"reply": "sorry"}, kind="apology", prefix="apology_")
    assert set(refund) == {"refund_task_id", "refund_artefact", "refund_artefact_digest", "refund_kind"}
    assert refund["refund_task_id"] != apology["apology_task_id"]


def test_an_empty_draft_is_not_put_in_front_of_anyone(bridge):
    with pytest.raises(ValueError, match="nothing to review"):
        hil_review(bridge, "", kind="draft")


# ---- a decision made at a desk -------------------------------------------


def test_a_desk_approval_is_read_and_applied_without_recording_it_again(bridge, coord):
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    timer = threading.Timer(0.05, desk_decides, (coord, "decide.approve", state["chap_task_id"]),
                            {"comment": "fine"})
    timer.start()
    try:
        decision = bridge.wait_for_decision(state["chap_task_id"], timeout=5, poll_seconds=0.01)
    finally:
        timer.join()
    assert decision == Decision(task_id=state["chap_task_id"], state="completed", kind="approve",
                                reviewer=REVIEWER, output={"reply": "draft"}, comment="fine")
    before = len(bridge.audit())
    assert bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"]) == {"reply": "draft"}
    assert len(bridge.audit()) == before


def test_a_desk_override_reaches_the_graph_with_its_rationale(bridge, coord):
    state = hil_review(bridge, {"reply": "Sorry."}, "draft")
    desk_decides(coord, "decide.override", state["chap_task_id"],
                 diff=[{"op": "replace", "path": "/reply", "value": "So sorry."}], rationale="warmer")
    decision = bridge.wait_for_decision(state["chap_task_id"], timeout=1)
    assert (decision.kind, decision.output, decision.comment) == ("override", {"reply": "So sorry."}, "warmer")
    assert bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"]) == {"reply": "So sorry."}


def test_a_revision_asked_at_the_desk_says_so_and_opens_the_next_round(bridge, coord):
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    desk_decides(coord, "decide.reject", state["chap_task_id"], comment="shorter", request_revision=True)
    decision = bridge.wait_for_decision(state["chap_task_id"], timeout=1)
    with pytest.raises(ReviewRejected) as raised:
        bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"])
    assert (raised.value.outcome, raised.value.state, raised.value.rationale) == (
        "revise", "in_progress", "shorter")
    again = hil_review(bridge, {"reply": "short"}, "draft", existing_task_id=state["chap_task_id"])
    assert again["chap_task_id"] == state["chap_task_id"]
    desk_decides(coord, "decide.approve", state["chap_task_id"])
    decision = bridge.wait_for_decision(state["chap_task_id"], timeout=1)
    assert bridge.apply_decision(state["chap_task_id"], decision, again["chap_artefact"]) == {"reply": "short"}


def test_an_abstention_raises_and_says_so(bridge, coord):
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    desk_decides(coord, "abstain.declare", state["chap_task_id"], reason="not my area")
    decision = bridge.wait_for_decision(state["chap_task_id"], timeout=1)
    with pytest.raises(ReviewRejected) as raised:
        bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"])
    assert (raised.value.outcome, raised.value.rationale) == ("abstain", "not my area")


def test_a_wait_that_stops_leaves_the_review_open_for_the_resumed_graph(bridge, coord):
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    with pytest.raises(TimeoutError):
        bridge.wait_for_decision(state["chap_task_id"], timeout=0.05, poll_seconds=0.01)
    assert task_of(coord, state["chap_task_id"]).state == "review_requested"
    assert bridge.withdraw(state["chap_task_id"]) is None
    assert task_of(coord, state["chap_task_id"]).state == "in_progress"


def test_a_decision_written_by_another_process_is_read(tmp_path):
    """The desk is another process with a coordinator of its own over the
    same SQLite file; the bridge's coordinator reads it through dispatch."""
    path = tmp_path / "chap.sqlite"
    here = Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True))
    bridge = ChapBridge(here, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    desk = Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True))
    desk_decides(desk, "decide.approve", state["chap_task_id"], comment="ok")
    decision = bridge.wait_for_decision(state["chap_task_id"], timeout=2, poll_seconds=0.01)
    assert (decision.kind, decision.comment) == ("approve", "ok")


def test_wait_for_decision_reads_a_client_with_task(coord):
    class DeskClient:
        def __init__(self) -> None:
            self.reads = 0

        def task(self, task_id):
            self.reads += 1
            if self.reads < 3:
                return {"task_id": task_id, "state": "review_requested", "output": None,
                        "review": {"decisions": []}}
            return {"task_id": task_id, "state": "completed", "output": {"reply": "edited"},
                    "review": {"decisions": [{"reviewer": REVIEWER, "kind": "override", "tags": ["tone"],
                                              "comment": "warmer", "rationale": "warmer"}]}}

    client = DeskClient()
    bridge = ChapBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER, client=client)
    decision = bridge.wait_for_decision("tsk_remote", timeout=5, poll_seconds=0.01)
    assert client.reads == 3
    assert (decision.kind, decision.output, decision.tags) == ("override", {"reply": "edited"}, ("tone",))


def test_wait_for_decision_on_an_unknown_task(bridge):
    with pytest.raises(KeyError):
        bridge.wait_for_decision("tsk_none", timeout=1)


def test_wait_for_decision_needs_something_to_read(coord):
    class DispatchOnly:
        def dispatch(self, envelope):
            return coord.dispatch(envelope)

    bridge = ChapBridge(DispatchOnly(), workspace=WS, agent=AGENT, reviewer=REVIEWER)
    with pytest.raises(TypeError, match="task\\(task_id\\)"):
        bridge.wait_for_decision("tsk_any", timeout=1)


# ---- a decision made in your own interface ---------------------------------


def test_a_resume_approval_is_recorded_as_recorded_and_bound_to_the_draft(bridge, coord):
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    assert bridge.apply_decision(state["chap_task_id"], "approve", state["chap_artefact"]) == {"reply": "draft"}
    [params] = calls(bridge, "decide.approve")
    assert params["from"] == REVIEWER
    assert params["via"] == "bridge-record"
    assert params["approved_artefact_digest"] == content_hash({"reply": "draft"})
    assert (task_of(coord, state["chap_task_id"]).state, coord.workspaces[WS].members[REVIEWER].type) == (
        "completed", "human")


def test_a_resume_rejection_raises_and_carries_nothing_on(bridge, coord):
    state = hil_review(bridge, {"reply": "rude"}, "draft")
    with pytest.raises(ReviewRejected) as raised:
        bridge.apply_decision(state["chap_task_id"], {"action": "reject", "rationale": "tone too rude"},
                              state["chap_artefact"])
    assert (raised.value.task_id, raised.value.rationale, raised.value.reviewer, raised.value.outcome) == (
        state["chap_task_id"], "tone too rude", REVIEWER, "reject")
    assert calls(bridge, "decide.reject")[0]["comment"] == "tone too rude"
    assert (task_of(coord, state["chap_task_id"]).state, task_of(coord, state["chap_task_id"]).output) == (
        "declined", None)


def test_a_resume_can_ask_for_another_draft(bridge, coord):
    state = hil_review(bridge, {"reply": "long"}, "draft")
    with pytest.raises(ReviewRejected) as raised:
        bridge.apply_decision(state["chap_task_id"],
                              {"action": "reject", "rationale": "shorter", "request_revision": True},
                              state["chap_artefact"])
    assert raised.value.outcome == "revise"
    assert task_of(coord, state["chap_task_id"]).state == "in_progress"


def test_a_resume_override_returns_the_patched_draft(bridge):
    state = hil_review(bridge, {"reply": "Sorry."}, "draft")
    applied = bridge.apply_decision(state["chap_task_id"], {
        "diff": [{"op": "replace", "path": "/reply", "value": "I sincerely apologise."}],
        "rationale": "tone too curt", "tags": ["tone-warmed"], "intent_preserved": True,
    }, state["chap_artefact"])
    assert applied == {"reply": "I sincerely apologise."}
    [params] = calls(bridge, "decide.override")
    assert (params["approved_artefact_digest"], params["via"], params["rationale"]) == (
        content_hash({"reply": "Sorry."}), "bridge-record", "tone too curt")


def test_a_decision_bound_to_the_draft_shown_carries_on_with_that_draft(bridge, coord):
    """The interface sends back the digest of the draft it showed, and the
    graph's state holds another by the time it resumes. The decision is on
    the draft shown, and that is what carries on."""
    state = hil_review(bridge, {"reply": "refund 100"}, "draft")
    reply = bridge.apply_decision(state["chap_task_id"],
                                  {"action": "approve", "approved_artefact_digest": state["chap_artefact_digest"]},
                                  {"reply": "refund 1000"})
    assert reply == {"reply": "refund 100"}
    assert task_of(coord, state["chap_task_id"]).output == {"reply": "refund 100"}


def test_a_decision_on_another_draft_is_refused(bridge, coord):
    state = hil_review(bridge, {"reply": "We will refund you 100 EUR."}, "draft")
    with pytest.raises(ChapBridgeError) as raised:
        bridge.apply_decision(state["chap_task_id"], "approve", {"reply": "We will refund you 1000 EUR."})
    assert raised.value.code == -32074
    with pytest.raises(ChapError) as raised:
        bridge.apply_decision(state["chap_task_id"], {
            "action": "approve", "approved_artefact_digest": content_hash({"reply": "another draft"})},
            state["chap_artefact"])
    assert raised.value.code == -32074
    assert task_of(coord, state["chap_task_id"]).state == "review_requested"


def test_a_node_that_runs_again_reads_its_decision_back(bridge, coord):
    """The node recorded the decision and then failed, so the graph runs it
    again with the same resume value. The decision on the chain is read
    back, and nothing is recorded or refused a second time."""
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    payload = {"diff": [{"op": "replace", "path": "/reply", "value": "edited"}], "rationale": "r"}
    first = bridge.apply_decision(state["chap_task_id"], payload, state["chap_artefact"])
    before = len(bridge.audit())
    assert bridge.apply_decision(state["chap_task_id"], payload, state["chap_artefact"]) == first
    rejected = hil_review(bridge, {"reply": "rude"}, "draft")
    for _ in range(2):
        with pytest.raises(ReviewRejected):
            bridge.apply_decision(rejected["chap_task_id"], "reject", rejected["chap_artefact"])
    assert len(calls(bridge, "decide.reject")) == 1
    # The second draft's task.create and task.complete, and one rejection.
    assert len(bridge.audit()) == before + 3


def test_nothing_is_recorded_in_a_persons_name_where_people_sign(tmp_path):
    pytest.importorskip("cryptography")
    coord = Coordinator(CoordinatorOptions(enable_chain=True, require_signatures=True,
                                           signature_policy="humans"))
    bridge = ChapBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    # Bob joins from the desk, with his own key, as a person must where people sign.
    bob = Signer.load_or_create("human:bob@example.org", tmp_path / "keys")
    coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": WS, "from": "human:bob@example.org", "type": "human",
                               "jwks": {"keys": [bob.public_jwk]}}})
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    with pytest.raises(PersonRequired):
        bridge.apply_decision(state["chap_task_id"], "approve", state["chap_artefact"])
    # Alice was not joined with no key, so her own name is still hers.
    assert REVIEWER not in coord.workspaces[WS].members
    assert task_of(coord, state["chap_task_id"]).state == "review_requested"


# ---- payloads -----------------------------------------------------------


@pytest.mark.parametrize("payload, message", [
    ({"action": "override"}, "needs a 'diff'"),
    ({"action": "frobnicate"}, "unknown decision action"),
    ({"decision": "reject"}, "explicit 'action'"),
])
def test_a_payload_that_says_nothing_clear_is_not_recorded(bridge, coord, payload, message):
    state = hil_review(bridge, {"reply": "draft"}, "draft")
    with pytest.raises(ValueError, match=message):
        bridge.apply_decision(state["chap_task_id"], payload, state["chap_artefact"])
    assert task_of(coord, state["chap_task_id"]).state == "review_requested"


def test_a_patch_that_does_not_apply_is_refused(bridge):
    state = hil_review(bridge, {"x": 1}, "draft")
    with pytest.raises(ChapError) as raised:
        bridge.apply_decision(state["chap_task_id"], {
            "diff": [{"op": "replace", "path": "/missing/path", "value": 1}], "rationale": "fails"},
            state["chap_artefact"])
    assert raised.value.code == -32012


def test_the_reviewer_is_a_person_and_not_the_agent(coord):
    with pytest.raises(ValueError, match="human:"):
        ChapBridge(coord, workspace=WS, agent=AGENT, reviewer="agent:bot#v1")
    with pytest.raises(ValueError, match="'agent:' or 'service:'"):
        ChapBridge(coord, workspace=WS, agent=REVIEWER, reviewer=REVIEWER)


def test_chap_bridge_error_is_the_coordinators_refusal():
    assert ChapBridgeError is ChapError


def test_every_call_has_an_id_of_its_own(coord):
    ids = []

    class Recording:
        def dispatch(self, envelope):
            ids.append(envelope["id"])
            return coord.dispatch(envelope)

    bridge = ChapBridge(Recording(), workspace=WS, agent=AGENT, reviewer=REVIEWER)
    bridge.audit()
    bridge.audit()
    assert len(ids) == len(set(ids)) == 4


# ---- over HTTP, at a desk where people sign ----------------------------------


@pytest.fixture
def desk(tmp_path, monkeypatch):
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN",
                 "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    # A person's first join takes the code the desk server prints at start.
    monkeypatch.setenv("CHAP_ENROLMENT_CODE", ENROL)
    path = tmp_path / "chap.config.json"
    path.write_text(json.dumps({
        "workspace": WS, "profiles": ["core/1.0", "review/1.0"], "store": ":memory:", "port": 0,
        "require_signatures": "humans", "chain": True,
        "humans": [{"uri": REVIEWER, "display_name": "Alice", "role": "reviewer"}], "agent": {"uri": AGENT},
    }), encoding="utf-8")
    config = load_config(path)
    server = make_server(config, make_coordinator(config))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield HttpCoordinator(f"http://127.0.0.1:{server.server_port}/chap")
    finally:
        server.shutdown()
        server.server_close()


def test_the_bridge_works_against_a_desk_where_people_sign(desk, tmp_path):
    pytest.importorskip("cryptography")
    alice = Participant(desk, WS, REVIEWER, Signer.load_or_create(REVIEWER, tmp_path / "keys"),
                        enrolment=ENROL)
    alice.join("human", "reviewer")
    for coord in (desk, Participant(desk, WS, AGENT)):
        bridge = ChapBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
        state = hil_review(bridge, {"reply": "refund 100"}, "draft")
        threading.Timer(0.05, alice.call, ("decide.override",), {
            "task_id": state["chap_task_id"], "rationale": "cap",
            "diff": [{"op": "replace", "path": "/reply", "value": "refund 50"}]}).start()
        decision = bridge.wait_for_decision(state["chap_task_id"], timeout=5, poll_seconds=0.02)
        assert bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"]) == {
            "reply": "refund 50"}
        with pytest.raises(PersonRequired):
            bridge.apply_decision(hil_review(bridge, {"reply": "x"}, "draft")["chap_task_id"], "approve", {"reply": "x"})
