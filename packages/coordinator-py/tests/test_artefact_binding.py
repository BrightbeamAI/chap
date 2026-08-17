"""
review/1.0: binding a decision to the content it decided on.

Two related gaps, reported against 9e7af2b by Iman Schrock (EMILIA) from the
CHAP-to-AEB interoperability profile, and tracked as #71 and #72.

A plain ``decide.approve`` bound ``task_id`` and no content, so a relying party
could not tell what was approved. And ``review.request`` had no guard, so the
artefact under an open review could be replaced with the decision envelope
carrying nothing that would detect the swap.

Mirrors ``packages/coordinator/tests/artefact_binding.test.ts`` case for case,
so a divergence between the two implementations fails on one side.
"""
from __future__ import annotations

import pytest

from chap_coordinator.canonical import content_hash
from chap_coordinator.coordinator import Coordinator, CoordinatorOptions

DRAFT = {"severity": "warning", "text": "the reviewed text"}
SWAPPED = {"severity": "critical", "text": "something else entirely"}


def setup():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))

    def send(method, params):
        return c.dispatch({"jsonrpc": "2.0", "id": f"t-{method}", "method": method, "params": params})

    send("workspace.create", {"workspace": "wsp_b"})
    for frm, role in (("human:alice", "owner"), ("human:bob", "reviewer"),
                      ("human:carol", "reviewer"), ("agent:bot", "drafter")):
        send("participant.join", {"workspace": "wsp_b", "from": frm, "role": role,
                                  "type": "human" if frm.startswith("human:") else "agent"})
    tid = send("task.create", {"workspace": "wsp_b", "from": "human:alice", "kind": "draft",
                               "input": {}, "assignee": "agent:bot"})["result"]["task_id"]
    send("task.update", {"workspace": "wsp_b", "from": "agent:bot", "task_id": tid, "state": "in_progress"})
    send("task.complete", {"workspace": "wsp_b", "from": "agent:bot", "task_id": tid,
                           "output": DRAFT, "confidence": "0.9"})
    return c, send, tid


def open_review(send, tid, artefact=DRAFT, to="human:bob"):
    return send("review.request", {"workspace": "wsp_b", "from": "agent:bot",
                                   "task_id": tid, "to": to, "artefact": artefact})


# ---- #72: the artefact under an open review cannot be swapped ----

def test_re_request_with_different_content_is_refused():
    c, send, tid = setup()
    open_review(send, tid)
    r = open_review(send, tid, SWAPPED)
    assert r["error"]["code"] == -32014
    assert "already open" in r["error"]["message"]
    assert c.get_workspace("wsp_b").tasks[tid].pending_artefact == DRAFT


def test_re_request_with_same_content_widens_the_reviewer_set():
    c, send, tid = setup()
    open_review(send, tid)
    r = open_review(send, tid, DRAFT, to="human:carol")
    assert "error" not in r
    assert r["result"]["amended"] is True
    assert r["result"]["requested_to"] == ["human:bob", "human:carol"]

    decided = send("decide.approve", {"workspace": "wsp_b", "from": "human:carol", "task_id": tid})
    assert "error" not in decided
    assert decided["result"]["state"] == "completed"


def test_decision_rule_cannot_change_under_an_open_review():
    _, send, tid = setup()
    open_review(send, tid)
    r = send("review.request", {"workspace": "wsp_b", "from": "agent:bot", "task_id": tid,
                                "to": "human:carol", "artefact": DRAFT, "rule": "quorum:2"})
    assert r["error"]["code"] == -32014
    assert "decision rule" in r["error"]["message"]


# ---- #71: an approval binds the content it approved ----

def test_matching_digest_is_accepted():
    _, send, tid = setup()
    open_review(send, tid)
    r = send("decide.approve", {"workspace": "wsp_b", "from": "human:bob", "task_id": tid,
                                "approved_artefact_digest": content_hash(DRAFT)})
    assert "error" not in r
    assert r["result"]["state"] == "completed"


def test_mismatched_digest_is_refused_and_changes_nothing():
    c, send, tid = setup()
    open_review(send, tid)
    r = send("decide.approve", {"workspace": "wsp_b", "from": "human:bob", "task_id": tid,
                                "approved_artefact_digest": content_hash(SWAPPED)})
    assert r["error"]["code"] == -32074
    task = c.get_workspace("wsp_b").tasks[tid]
    assert task.state == "review_requested", "no state change on a refused decision"
    assert task.review.decisions == [], "nothing recorded on a refused decision"


def test_absent_digest_behaves_exactly_as_before():
    _, send, tid = setup()
    open_review(send, tid)
    r = send("decide.approve", {"workspace": "wsp_b", "from": "human:bob", "task_id": tid})
    assert "error" not in r
    assert r["result"]["state"] == "completed"


def test_non_string_digest_is_invalid_params():
    _, send, tid = setup()
    open_review(send, tid)
    r = send("decide.approve", {"workspace": "wsp_b", "from": "human:bob", "task_id": tid,
                                "approved_artefact_digest": 12345})
    assert r["error"]["code"] == -32602


def test_override_binds_the_base_artefact_the_same_way():
    _, send, tid = setup()
    open_review(send, tid)
    bad = send("decide.override", {"workspace": "wsp_b", "from": "human:bob", "task_id": tid,
                                   "diff": [{"op": "replace", "path": "/severity", "value": "info"}],
                                   "rationale": "false positive",
                                   "approved_artefact_digest": content_hash(SWAPPED)})
    assert bad["error"]["code"] == -32074

    ok = send("decide.override", {"workspace": "wsp_b", "from": "human:bob", "task_id": tid,
                                  "diff": [{"op": "replace", "path": "/severity", "value": "info"}],
                                  "rationale": "false positive",
                                  "approved_artefact_digest": content_hash(DRAFT)})
    assert "error" not in ok


def test_digest_is_the_same_construction_the_chain_uses():
    import re
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash(DRAFT))
