"""
Refused calls on the chain.

A coordinator from 0.3.0 records a member's refused call when it was a
governed attempt, under ``request`` with an ``outcome`` beside it
(SPECIFICATION 10.1). The projection lists those attempts and replays none of
them, so a refused approval never counts as a decision. The chains here are
driven through the real coordinator, read both as a snapshot and as an
``audit.read`` export.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

from chap_analytics import frames, from_coordinator, from_json, redact_artefacts  # noqa: E402

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "audit-scitt/1.0"]
MARKER = "refused-call-content-marker"


def _workspace():
    coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def send(method, params=None, actor="human:ana"):
        return coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                               "params": {"workspace": "w", "from": actor, **(params or {})}})

    send("workspace.create", {"profiles": PROFILES})
    for uri, kind in (("human:ana", "human"), ("human:bo", "human"), ("agent:drafter", "agent")):
        send("participant.join", {"type": kind}, uri)
    tid = send("task.create", {"kind": "reply", "input": {}, "assignee": "agent:drafter"},
               "agent:drafter")["result"]["task_id"]
    send("task.update", {"task_id": tid, "state": "in_progress"}, "agent:drafter")
    send("review.request", {"task_id": tid, "artefact": {"body": "draft"}, "to": "human:bo"},
         "agent:drafter")
    # ana is not the addressed reviewer: refused -32011, and recorded.
    assert send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:ana")["error"]["code"] == -32011
    assert "result" in send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:bo")
    # A second task, cancelled, then asked back into review with content in
    # the artefact: refused as not reviewable, and recorded.
    t2 = send("task.create", {"kind": "reply", "input": {}, "assignee": "agent:drafter"},
              "agent:drafter")["result"]["task_id"]
    send("control.cancel", {"task_id": t2, "reason": "duplicate"}, "human:ana")
    refused = send("review.request", {"task_id": t2, "artefact": {"body": MARKER}, "to": "human:bo"},
                   "agent:drafter")
    assert refused["error"]["code"] == -32010
    return coord, send, tid


@pytest.fixture(params=["snapshot", "audit.read"])
def chain(request, tmp_path):
    coord, send, _ = _workspace()
    if request.param == "snapshot":
        return from_coordinator(coord, "w")
    path = tmp_path / "export.json"
    path.write_text(json.dumps(send("audit.read")["result"]), encoding="utf-8")
    return from_json(str(path), workspace="w")


def test_refusals_are_listed_with_their_codes(chain):
    f = frames(chain)
    r = f.refusals.sort_values("seq")
    assert list(r["method"]) == ["decide.approve", "review.request"]
    assert list(r["code"]) == [-32011, -32010]
    assert list(r["actor"]) == ["human:ana", "agent:drafter"]
    assert bool(r["chained"].all())


def test_a_refused_approval_is_not_a_decision(chain):
    f = frames(chain)
    approvals = f.decisions[f.decisions["kind"] == "approve"]
    assert list(approvals["reviewer"]) == ["human:bo"]
    assert "human:ana" not in set(f.decisions["reviewer"].dropna())


def test_events_and_refusals_together_hold_the_whole_log(chain):
    f = frames(chain)
    seqs = sorted(list(f.events["seq"]) + list(f.refusals["seq"]))
    assert seqs == list(range(len(seqs)))
    assert not set(f.events["seq"]) & set(f.refusals["seq"])


def test_the_redactor_reaches_a_refused_call():
    coord, _, _ = _workspace()
    chain = from_coordinator(coord, "w", redact=redact_artefacts)
    assert MARKER not in json.dumps(chain.events, default=str)
    assert MARKER not in json.dumps(chain.state, default=str)


def test_the_summary_names_refusals():
    coord, _, _ = _workspace()
    text = from_coordinator(coord, "w").summary()
    assert "decide.approve (refused)" in text
    assert "review.request (refused)" in text
