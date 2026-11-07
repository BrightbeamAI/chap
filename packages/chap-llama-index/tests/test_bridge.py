"""
Tests for chap-llama-index that run without llama-index-workflows:
recording a decision made in your own code, which reads events through
event.get(), so small stand-ins drive the path the real events would; and
the bridge made against a desk. tests/test_workflow.py runs request_response
in a real Workflow.
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
from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_llama_index import ChapBridgeError, ChapError, ChapHitlBridge, PersonRequired

WS = "wsp_li_test"
AGENT = "agent:writer#v1"
REVIEWER = "human:alice@example.org"


class Event:
    """A Workflows event stand-in: fields read with .get(), all of them with to_dict()."""

    def __init__(self, **data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return self._data


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True, enable_chain=True))


@pytest.fixture
def bridge(coord):
    return ChapHitlBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)


def calls(bridge, method):
    return [e["envelope"]["params"] for e in bridge.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


PROPOSED = {"amount": 100, "to": "acct-9"}
ASKED = Event(prefix="approve payment? ", proposed=PROPOSED)


def test_the_bridge_joins_the_agent_and_nobody_else(coord, bridge):
    assert set(coord.workspaces[WS].members) == {AGENT}
    ChapHitlBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    assert set(coord.workspaces[WS].members) == {AGENT}


def test_an_approval_is_recorded_as_recorded_and_bound_to_the_output(coord, bridge):
    seen = []
    bridge.on_envelope = lambda m, p: seen.append(m)
    task_id = bridge.record_decision(ASKED, Event(response=PROPOSED), decision="approve")
    assert [m for m in seen if m.startswith(("task.", "review.", "decide."))] == [
        "task.create", "task.complete", "decide.approve"]
    [params] = calls(bridge, "decide.approve")
    assert (params["from"], params["via"], params["approved_artefact_digest"]) == (
        REVIEWER, "bridge-record", content_hash(PROPOSED))
    assert coord.workspaces[WS].tasks[task_id].output == PROPOSED


def test_an_override_diffs_proposed_against_returned(coord, bridge):
    response = Event(response={"amount": 50, "to": "acct-9"}, rationale="capped", tags=["limit"],
                     intent_preserved=True)
    task_id = bridge.record_decision(ASKED, response, decision="override")
    [params] = calls(bridge, "decide.override")
    assert params["diff"] == [{"op": "replace", "path": "/amount", "value": 50}]
    assert (params["rationale"], params["tags"], params["intent_preserved"], params["via"]) == (
        "capped", ["limit"], True, "bridge-record")
    assert coord.workspaces[WS].tasks[task_id].output == {"amount": 50, "to": "acct-9"}


def test_an_override_that_changes_nothing_is_an_approval(bridge):
    bridge.record_decision(ASKED, Event(response=dict(PROPOSED)), decision="override")
    assert (len(calls(bridge, "decide.approve")), calls(bridge, "decide.override")) == (1, [])


def test_a_rejection_takes_its_note_from_the_response(bridge):
    bridge.record_decision(ASKED, Event(response="too much"), decision="reject")
    assert calls(bridge, "decide.reject")[0]["comment"] == "too much"


def test_an_explicit_proposal_and_approver(coord, bridge):
    bridge.record_decision(Event(prefix="x"), Event(user_name="human:bob@example.org"),
                           decision="approve", proposed={"text": "hello"})
    [params] = calls(bridge, "decide.approve")
    assert (params["from"], params["approved_artefact_digest"]) == (
        "human:bob@example.org", content_hash({"text": "hello"}))
    assert coord.workspaces[WS].members["human:bob@example.org"].type == "human"


def test_what_cannot_be_recorded_is_refused(bridge):
    with pytest.raises(ValueError, match="decision must be"):
        bridge.record_decision(ASKED, Event(), decision="maybe")
    with pytest.raises(ValueError, match="returned"):
        bridge.record_decision(ASKED, Event(), decision="override")
    with pytest.raises(ValueError, match="'human:' URI"):
        bridge.record_decision(ASKED, Event(user_name="sam"), decision="approve")


def test_nothing_is_recorded_in_a_persons_name_where_people_sign():
    coord = Coordinator(CoordinatorOptions(enable_chain=True, require_signatures=True, signature_policy="humans"))
    bridge = ChapHitlBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    with pytest.raises(PersonRequired):
        bridge.record_decision(ASKED, Event(response=PROPOSED), decision="approve")
    assert set(coord.workspaces[WS].members) == {AGENT} and coord.workspaces[WS].tasks == {}


def test_refusals_are_chap_errors_and_the_chain_is_linked(bridge):
    bridge.record_decision(ASKED, Event(response=PROPOSED), decision="approve")
    assert all("prev_hash" in e for e in bridge.audit())
    assert ChapBridgeError is ChapError


def test_every_call_has_an_id_of_its_own(coord):
    ids = []

    class Recording:
        def dispatch(self, envelope):
            ids.append(envelope["id"])
            return coord.dispatch(envelope)

    bridge = ChapHitlBridge(Recording(), workspace="wsp_li_ids", agent=AGENT, reviewer=REVIEWER)
    bridge.audit()
    bridge.audit()
    assert len(ids) == len(set(ids)) == 4


@pytest.fixture
def desk(tmp_path, monkeypatch):
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
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


def test_the_bridge_is_made_against_a_desk_where_people_sign_and_records_nothing_there(desk, tmp_path):
    pytest.importorskip("cryptography")
    for coord in (desk, Participant(desk, WS, AGENT)):
        bridge = ChapHitlBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
        with pytest.raises(PersonRequired):
            bridge.record_decision(ASKED, Event(response=PROPOSED), decision="approve")
    alice = Participant(desk, WS, REVIEWER, Signer.load_or_create(REVIEWER, tmp_path / "keys"))
    assert alice.join("human", "reviewer") == {"joined": True, "as": REVIEWER}
