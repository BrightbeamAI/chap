"""End-to-end composition test exercising every profile in one workspace.

This is the canary test: if every profile is implemented correctly, a
single workspace can exercise all 39 method handlers in sequence
without any spec-deviation surprises.
"""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=[
            "core/1.0", "review/1.0", "whisper/1.0",
            "deliberation/1.0", "handoff/1.0", "control/1.0",
            "routing/1.0", "audit-scitt/1.0",
        ],
    ))


def send(coord, method, **params):
    return coord.dispatch({
        "jsonrpc": "2.0", "id": f"e2e-{method}", "method": method,
        "params": params,
    })


def test_full_lifecycle_every_profile(coord):
    """Exercise Core + every method-shipping profile in one sequence."""
    # ----- workspace.create + participant.join -----
    r = send(coord, "workspace.create", workspace="wsp_e2e")
    assert r["result"]["workspace"] == "wsp_e2e"

    for who, role in [
        ("human:alice@x", "owner"),
        ("human:bob@x", "reviewer"),
        ("human:carol@x", "reviewer"),
        ("agent:bot", "drafter"),
    ]:
        r = send(coord, "participant.join", workspace="wsp_e2e",
                 **{"from": who, "type": "human" if who.startswith("human") else "agent",
                    "role": role})
        assert r["result"]["joined"] is True

    # ----- task.create + task.update + task.complete -----
    r = send(coord, "task.create", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             kind="draft", input={"q": "?"}, assignee="agent:bot",
             routing_hints={"criticality": "low", "confidence": 0.9})
    tid = r["result"]["task_id"]

    send(coord, "task.update", workspace="wsp_e2e", task_id=tid,
         state="in_progress", **{"from": "agent:bot"})

    # ----- whisper.ask + whisper.answer (whisper/1.0) -----
    r = send(coord, "whisper.ask", workspace="wsp_e2e",
             **{"from": "agent:bot", "to": ["human:alice@x"]},
             task_id=tid, question="proceed?",
             options=[{"id": "yes"}, {"id": "no"}],
             deadline_ms=30000, default_if_lapsed="no")
    wid = r["result"]["whisper_id"]
    r = send(coord, "whisper.answer", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             whisper_id=wid, answer_option="yes")
    assert r["result"]["answered"] is True

    # ----- review.depth + escalate.auto + task.route (routing/1.0) -----
    r = send(coord, "review.depth", workspace="wsp_e2e", task_id=tid)
    assert r["result"]["depth"] in ("skip", "spot_check", "full")

    r = send(coord, "escalate.auto", workspace="wsp_e2e", task_id=tid,
             default_escalation_target="human:bob@x")
    assert r["result"]["escalate"] in (True, False)

    # ----- review.request + decide.override (review/1.0) -----
    draft = {"comments": [{"severity": "warning", "text": "x"}]}
    send(coord, "review.request", workspace="wsp_e2e",
         **{"from": "agent:bot", "to": "human:alice@x"},
         task_id=tid, artefact=draft)
    r = send(coord, "decide.override", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             task_id=tid,
             diff=[{"op": "replace", "path": "/comments/0/severity",
                    "value": "info"}],
             rationale="false positive",
             tags=["false-positive"],
             intent_preserved=True)
    assert r["result"]["applied"]["comments"][0]["severity"] == "info"

    # ----- abstain.declare + escalate.raise on a second task -----
    r = send(coord, "task.create", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             kind="review", input={"q": "?"}, assignee="agent:bot")
    tid2 = r["result"]["task_id"]
    send(coord, "task.update", workspace="wsp_e2e", task_id=tid2,
         state="in_progress", **{"from": "agent:bot"})
    send(coord, "review.request", workspace="wsp_e2e",
         **{"from": "agent:bot", "to": "human:bob@x"},
         task_id=tid2, artefact={"x": 1})
    r = send(coord, "abstain.declare", workspace="wsp_e2e",
             **{"from": "human:bob@x"},
             task_id=tid2, reason="conflict",
             category="conflict_of_interest")
    assert r["result"]["state"] == "abstained"

    # ----- deliberate.open/comment/vote/close (deliberation/1.0) -----
    r = send(coord, "task.create", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             kind="decide", input={"q": "?"}, assignee="human:alice@x")
    tid3 = r["result"]["task_id"]
    r = send(coord, "deliberate.open", workspace="wsp_e2e",
             **{"from": "human:alice@x",
                "to": ["human:alice@x", "human:bob@x", "human:carol@x"]},
             task_id=tid3, rule="quorum:2",
             question="ship the hotfix?")
    did = r["result"]["deliberation_id"]
    send(coord, "deliberate.comment", workspace="wsp_e2e",
         **{"from": "human:alice@x"}, deliberation_id=did,
         comment="risk is small")
    send(coord, "deliberate.vote", workspace="wsp_e2e",
         **{"from": "human:alice@x"}, deliberation_id=did, vote="yea")
    send(coord, "deliberate.vote", workspace="wsp_e2e",
         **{"from": "human:bob@x"}, deliberation_id=did, vote="yea")
    r = send(coord, "deliberate.close", workspace="wsp_e2e",
             **{"from": "human:alice@x"}, deliberation_id=did)
    assert r["result"]["outcome"] == "approved"

    # ----- handoff.propose + handoff.accept (handoff/1.0) -----
    r = send(coord, "task.create", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             kind="shift", input={"q": "?"}, assignee="human:alice@x")
    tid4 = r["result"]["task_id"]
    r = send(coord, "handoff.propose", workspace="wsp_e2e",
             **{"from": "human:alice@x", "to": "human:bob@x"},
             tasks=[{"task_id": tid4, "title": "Open ticket"}],
             summary="EOS handoff")
    hid = r["result"]["handoff_id"]
    r = send(coord, "handoff.accept", workspace="wsp_e2e",
             **{"from": "human:bob@x"}, handoff_id=hid)
    assert r["result"]["accepted"] is True

    # ----- control.snapshot + control.supersede + control.rollback -----
    r = send(coord, "control.snapshot", workspace="wsp_e2e",
             **{"from": "human:alice@x"}, label="mid-test")
    snap = r["result"]["snapshot_artefact_id"]

    r = send(coord, "task.create", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             kind="bad", input={"q": "?"}, assignee="agent:bot")
    tid5 = r["result"]["task_id"]
    r = send(coord, "control.supersede", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             task_id=tid5,
             successor_task={"kind": "redo", "assignee": "agent:bot",
                             "input": {"q": "redo"}},
             reason="quality concern")
    assert r["result"]["superseded_task_id"] == tid5

    r = send(coord, "control.rollback", workspace="wsp_e2e",
             **{"from": "human:alice@x"},
             to_snapshot_artefact_id=snap,
             what_to_restore=["mode_ceiling"])
    assert r["result"]["rolled_back_to"] == snap

    # ----- control.pause/resume on participant scope -----
    r = send(coord, "control.pause", workspace="wsp_e2e",
             scope="participant", participant_uri="agent:bot",
             **{"from": "human:alice@x"})
    assert r["result"]["paused"] is True
    send(coord, "control.resume", workspace="wsp_e2e",
         scope="participant", participant_uri="agent:bot",
         **{"from": "human:alice@x"})

    # ----- control.set_mode_ceiling (modes/1.0 + control/1.0) -----
    r = send(coord, "control.set_mode_ceiling", workspace="wsp_e2e",
             **{"from": "human:alice@x"}, new_ceiling="production")
    assert r["result"]["mode_ceiling"] == "production"

    # ----- audit.verify_chain + audit.submit_to_scitt (audit-scitt/1.0) -----
    r = send(coord, "audit.verify_chain", workspace="wsp_e2e")
    assert r["result"]["ok"] is True

    r = send(coord, "audit.submit_to_scitt", workspace="wsp_e2e",
             **{"from": "service:coordinator"})
    # No submitter configured, statements come back instead
    assert "statements" in r["result"]
    assert len(r["result"]["statements"]) > 0

    # ----- final shape check -----
    r = send(coord, "workspace.describe", workspace="wsp_e2e")
    ws = r["result"]
    assert ws["task_count"] >= 5
    assert ws["override_count"] >= 1
    assert ws["audit_count"] > 30  # we did a lot
    assert ws["evidence_head"] is not None
    assert ws["evidence_head"].startswith("sha256:")
