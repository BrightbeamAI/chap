"""Tests for whisper, deliberation, handoff, control, routing, audit-scitt
exercising the spec-accurate field shapes from profiles/*.md."""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions


@pytest.fixture
def ready():
    """A coordinator with a workspace, three participants, and one task."""
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
    ))

    def send(method, **params):
        return coord.dispatch({
            "jsonrpc": "2.0", "id": f"t-{method}", "method": method,
            "params": params,
        })

    send("workspace.create", workspace="wsp_p",
         profiles=["core/1.0", "review/1.0", "whisper/1.0",
                   "deliberation/1.0", "handoff/1.0", "control/1.0",
                   "routing/1.0", "audit-scitt/1.0"])
    send("participant.join", workspace="wsp_p",
         **{"from": "human:alice@x", "type": "human", "role": "owner"})
    send("participant.join", workspace="wsp_p",
         **{"from": "human:bob@x", "type": "human", "role": "reviewer"})
    send("participant.join", workspace="wsp_p",
         **{"from": "agent:bot", "type": "agent", "role": "drafter"})
    r = send("task.create", workspace="wsp_p",
             **{"from": "human:alice@x", "kind": "k", "input": {}},
             assignee="agent:bot")
    return coord, send, r["result"]["task_id"]


# -------- whisper/1.0 --------

def test_whisper_ask_and_answer(ready):
    coord, send, tid = ready
    r = send("whisper.ask", workspace="wsp_p",
             **{"from": "agent:bot", "to": ["human:alice@x"]},
             task_id=tid,
             question="confirm policy v3?",
             options=[{"id": "yes"}, {"id": "no"}],
             deadline_ms=30000, default_if_lapsed="no")
    assert "result" in r
    wid = r["result"]["whisper_id"]

    # Spec field: answer_option (not 'answer')
    r = send("whisper.answer", workspace="wsp_p",
             **{"from": "human:alice@x"}, whisper_id=wid, answer_option="yes")
    assert r["result"]["answered"] is True

    # Cannot answer twice
    r = send("whisper.answer", workspace="wsp_p",
             **{"from": "human:alice@x"}, whisper_id=wid, answer_option="no")
    assert r["error"]["code"] == -32020  # WHISPER_ALREADY_ANSWERED


def test_whisper_option_not_in_set(ready):
    coord, send, tid = ready
    r = send("whisper.ask", workspace="wsp_p",
             **{"from": "agent:bot", "to": ["human:alice@x"]},
             task_id=tid,
             question="?",
             options=[{"id": "a"}, {"id": "b"}],
             deadline_ms=30000, default_if_lapsed="a")
    wid = r["result"]["whisper_id"]
    r = send("whisper.answer", workspace="wsp_p",
             **{"from": "human:alice@x"},
             whisper_id=wid, answer_option="c")
    assert r["error"]["code"] == -32022  # OPTION_NOT_IN_SET


def test_whisper_lapse_handling(ready):
    """check_whisper_lapses emits a notification and marks the whisper lapsed."""
    coord, send, tid = ready
    r = send("whisper.ask", workspace="wsp_p",
             **{"from": "agent:bot", "to": ["human:alice@x"]},
             task_id=tid,
             question="?", options=[{"id": "a"}, {"id": "b"}],
             deadline_ms=1, default_if_lapsed="a")
    wid = r["result"]["whisper_id"]
    # Advance time well past the deadline
    emitted = coord.check_whisper_lapses("wsp_p", now="2100-01-01T00:00:00.000Z")
    assert len(emitted) == 1
    # Answering a lapsed whisper now returns WHISPER_LAPSED
    r = send("whisper.answer", workspace="wsp_p",
             **{"from": "human:alice@x"}, whisper_id=wid, answer_option="a")
    assert r["error"]["code"] == -32021  # WHISPER_LAPSED


# -------- deliberation/1.0 --------

def test_deliberation_any_one_approves(ready):
    coord, send, tid = ready
    r = send("deliberate.open", workspace="wsp_p",
             **{"from": "human:alice@x", "to": ["human:alice@x", "human:bob@x"]},
             task_id=tid, rule="any_one_approves")
    did = r["result"]["deliberation_id"]
    send("deliberate.vote", workspace="wsp_p",
         **{"from": "human:alice@x"},
         deliberation_id=did, vote="yea")
    r = send("deliberate.close", workspace="wsp_p",
             **{"from": "human:alice@x"}, deliberation_id=did)
    assert r["result"]["outcome"] == "approved"
    assert r["result"]["tally"]["yea"] == 1


def test_deliberation_quorum(ready):
    coord, send, tid = ready
    r = send("deliberate.open", workspace="wsp_p",
             **{"from": "human:alice@x",
                "to": ["human:alice@x", "human:bob@x", "agent:bot"]},
             task_id=tid, rule="quorum:2")
    did = r["result"]["deliberation_id"]
    send("deliberate.vote", workspace="wsp_p",
         **{"from": "human:alice@x"}, deliberation_id=did, vote="yea")
    r = send("deliberate.close", workspace="wsp_p",
             **{"from": "human:alice@x"}, deliberation_id=did)
    assert r["result"]["outcome"] == "rejected"
    assert r["result"]["reason"] == "quorum not met"


def test_deliberation_veto(ready):
    coord, send, tid = ready
    r = send("deliberate.open", workspace="wsp_p",
             **{"from": "human:alice@x",
                "to": ["human:alice@x", "human:bob@x"]},
             task_id=tid,
             rule="weighted_vote_with_veto:1.0",
             weights={"human:alice@x": 1.0, "human:bob@x": 1.0},
             veto={"human:bob@x": True})
    did = r["result"]["deliberation_id"]
    send("deliberate.vote", workspace="wsp_p",
         **{"from": "human:alice@x"}, deliberation_id=did, vote="yea")
    send("deliberate.vote", workspace="wsp_p",
         **{"from": "human:bob@x"}, deliberation_id=did, vote="nay",
         veto_invoked=True)
    r = send("deliberate.close", workspace="wsp_p",
             **{"from": "human:alice@x"}, deliberation_id=did)
    assert r["result"]["outcome"] == "rejected"
    assert "human:bob@x" in r["result"]["vetoes"]


def test_deliberation_voter_not_in_list(ready):
    coord, send, tid = ready
    r = send("deliberate.open", workspace="wsp_p",
             **{"from": "human:alice@x", "to": ["human:alice@x"]},
             task_id=tid, rule="any_one_approves")
    did = r["result"]["deliberation_id"]
    r = send("deliberate.vote", workspace="wsp_p",
             **{"from": "human:bob@x"}, deliberation_id=did, vote="yea")
    assert r["error"]["code"] == -32030  # VOTER_NOT_IN_LIST


def test_deliberation_already_voted(ready):
    coord, send, tid = ready
    r = send("deliberate.open", workspace="wsp_p",
             **{"from": "human:alice@x",
                "to": ["human:alice@x", "human:bob@x"]},
             task_id=tid, rule="any_one_approves")
    did = r["result"]["deliberation_id"]
    send("deliberate.vote", workspace="wsp_p",
         **{"from": "human:alice@x"}, deliberation_id=did, vote="yea")
    r = send("deliberate.vote", workspace="wsp_p",
         **{"from": "human:alice@x"}, deliberation_id=did, vote="nay")
    assert r["error"]["code"] == -32031  # ALREADY_VOTED


def test_deliberation_unknown_rule(ready):
    coord, send, tid = ready
    r = send("deliberate.open", workspace="wsp_p",
             **{"from": "human:alice@x", "to": ["human:alice@x"]},
             task_id=tid, rule="made_up_rule")
    assert r["error"]["code"] == -32033  # UNKNOWN_RULE


# -------- handoff/1.0 --------

def test_handoff_propose_and_accept(ready):
    coord, send, tid = ready
    # Reassign to alice so she can hand off
    coord.workspaces["wsp_p"].tasks[tid].assignee = "human:alice@x"
    r = send("handoff.propose", workspace="wsp_p",
             **{"from": "human:alice@x", "to": "human:bob@x"},
             tasks=[{"task_id": tid, "title": "test"}],
             summary="hand it over")
    assert "result" in r
    hid = r["result"]["handoff_id"]

    r = send("handoff.accept", workspace="wsp_p",
             **{"from": "human:bob@x"},
             handoff_id=hid)
    assert r["result"]["accepted"] is True
    assert r["result"]["assignee"] == "human:bob@x"


def test_handoff_recipient_not_member(ready):
    coord, send, tid = ready
    coord.workspaces["wsp_p"].tasks[tid].assignee = "human:alice@x"
    r = send("handoff.propose", workspace="wsp_p",
             **{"from": "human:alice@x", "to": "human:not-joined@x"},
             tasks=[{"task_id": tid}])
    assert r["error"]["code"] == -32052


def test_handoff_tasks_not_assigned_to_proposer(ready):
    coord, send, tid = ready
    # bob proposes a task assigned to bot - should fail
    r = send("handoff.propose", workspace="wsp_p",
             **{"from": "human:bob@x", "to": "human:alice@x"},
             tasks=[{"task_id": tid}])
    assert r["error"]["code"] == -32050


def test_handoff_already_resolved(ready):
    coord, send, tid = ready
    coord.workspaces["wsp_p"].tasks[tid].assignee = "human:alice@x"
    r = send("handoff.propose", workspace="wsp_p",
             **{"from": "human:alice@x", "to": "human:bob@x"},
             tasks=[{"task_id": tid}])
    hid = r["result"]["handoff_id"]
    send("handoff.accept", workspace="wsp_p",
         **{"from": "human:bob@x"}, handoff_id=hid)
    r = send("handoff.accept", workspace="wsp_p",
             **{"from": "human:bob@x"}, handoff_id=hid)
    assert r["error"]["code"] == -32051


def test_handoff_multi_task(ready):
    coord, send, tid1 = ready
    # Create a second task
    r = send("task.create", workspace="wsp_p",
             **{"from": "human:alice@x", "kind": "k", "input": {}},
             assignee="agent:bot")
    tid2 = r["result"]["task_id"]
    # Reassign tid1 to alice so she can hand them off
    coord.workspaces["wsp_p"].tasks[tid1].assignee = "human:alice@x"
    coord.workspaces["wsp_p"].tasks[tid2].assignee = "human:alice@x"
    r = send("handoff.propose", workspace="wsp_p",
             **{"from": "human:alice@x", "to": "human:bob@x"},
             tasks=[{"task_id": tid1}, {"task_id": tid2}])
    hid = r["result"]["handoff_id"]
    r = send("handoff.accept", workspace="wsp_p",
             **{"from": "human:bob@x"}, handoff_id=hid)
    assert set(r["result"]["task_ids"]) == {tid1, tid2}
    assert coord.workspaces["wsp_p"].tasks[tid1].assignee == "human:bob@x"
    assert coord.workspaces["wsp_p"].tasks[tid2].assignee == "human:bob@x"


# -------- control/1.0 --------

def test_control_pause_resume_task(ready):
    coord, send, tid = ready
    send("task.update", workspace="wsp_p", task_id=tid,
         state="in_progress", **{"from": "agent:bot"})
    r = send("control.pause", workspace="wsp_p", scope="task", task_id=tid,
             **{"from": "human:alice@x"})
    assert r["result"]["state"] == "paused"
    r = send("control.resume", workspace="wsp_p", scope="task", task_id=tid,
             **{"from": "human:alice@x"})
    assert r["result"]["state"] == "in_progress"


def test_control_pause_participant(ready):
    coord, send, tid = ready
    r = send("control.pause", workspace="wsp_p", scope="participant",
             participant_uri="agent:bot",
             **{"from": "human:alice@x"})
    assert r["result"]["paused"] is True


def test_control_pause_workspace_blocks_new_work(ready):
    coord, send, tid = ready
    send("control.pause", workspace="wsp_p", scope="workspace",
         **{"from": "human:alice@x"})
    r = send("task.create", workspace="wsp_p",
             **{"from": "human:alice@x", "kind": "k", "input": {}},
             assignee="agent:bot")
    assert r["error"]["code"] == -32063  # WORKSPACE_PAUSED


def test_control_snapshot_returns_artefact_id(ready):
    coord, send, tid = ready
    r = send("control.snapshot", workspace="wsp_p", **{"from": "human:alice@x"},
             label="before-batch")
    assert r["result"]["snapshot_artefact_id"].startswith("art_")
    assert r["result"]["artefact"]["kind"] == "snapshot"


def test_control_rollback_uses_to_snapshot_artefact_id(ready):
    coord, send, tid = ready
    r = send("control.snapshot", workspace="wsp_p",
             **{"from": "human:alice@x"},
             label="before-batch", include=["mode_ceiling"])
    snap_id = r["result"]["snapshot_artefact_id"]
    r = send("control.rollback", workspace="wsp_p",
             **{"from": "human:alice@x"},
             to_snapshot_artefact_id=snap_id,
             what_to_restore=["mode_ceiling"])
    assert r["result"]["rolled_back_to"] == snap_id
    assert "mode_ceiling" in r["result"]["restored"]


def test_control_supersede_creates_successor(ready):
    coord, send, tid = ready
    r = send("control.supersede", workspace="wsp_p",
             **{"from": "human:alice@x"},
             task_id=tid,
             successor_task={"kind": "redo", "assignee": "agent:bot",
                             "input": {"redo": True}},
             reason="bot was buggy")
    assert r["result"]["superseded_task_id"] == tid
    new_id = r["result"]["new_task_id"]
    assert new_id in coord.workspaces["wsp_p"].tasks
    assert coord.workspaces["wsp_p"].tasks[new_id].supersedes == tid


def test_control_set_mode_ceiling_blocks_higher_mode(ready):
    coord, send, tid = ready
    r = send("control.set_mode_ceiling", workspace="wsp_p",
             **{"from": "human:alice@x"}, new_ceiling="trial")
    assert r["result"]["mode_ceiling"] == "trial"
    r = send("task.create", workspace="wsp_p",
             **{"from": "human:alice@x", "kind": "k", "input": {}},
             assignee="agent:bot", mode="production")
    assert r["error"]["code"] == -32040  # MODE_CEILING_EXCEEDED


# -------- routing/1.0 --------

def test_review_depth_high_criticality_returns_full(ready):
    coord, send, tid = ready
    # Set hints on the task first
    coord.workspaces["wsp_p"].tasks[tid].routing_hints = {
        "criticality": "critical", "confidence": "0.9",
    }
    r = send("review.depth", workspace="wsp_p", task_id=tid)
    assert r["result"]["depth"] == "full"
    assert r["result"]["decision_artefact"].startswith("art_")


def test_review_depth_low_crit_high_conf_skips(ready):
    coord, send, tid = ready
    coord.workspaces["wsp_p"].tasks[tid].routing_hints = {
        "criticality": "low", "confidence": "0.97",
    }
    r = send("review.depth", workspace="wsp_p", task_id=tid)
    assert r["result"]["depth"] == "skip"


def test_review_depth_spot_check_has_sampling(ready):
    coord, send, tid = ready
    coord.workspaces["wsp_p"].tasks[tid].routing_hints = {
        "criticality": "low", "confidence": "0.85",
    }
    r = send("review.depth", workspace="wsp_p", task_id=tid)
    assert r["result"]["depth"] == "spot_check"
    assert 0.0 < r["result"]["sampling_probability"] <= 1.0


def test_task_route_picks_eligible_and_updates_assignee(ready):
    coord, send, tid = ready
    r = send("task.route", workspace="wsp_p", task_id=tid,
             candidates=["nobody@nowhere", "human:bob@x"])
    assert r["result"]["selected"] == "human:bob@x"
    assert coord.workspaces["wsp_p"].tasks[tid].assignee == "human:bob@x"
    # And a route_decision artefact was emitted
    art_id = r["result"]["decision_artefact"]
    assert art_id.startswith("art_")


def test_task_route_candidates_empty(ready):
    coord, send, tid = ready
    r = send("task.route", workspace="wsp_p", task_id=tid, candidates=[])
    assert r["error"]["code"] == -32513  # CANDIDATES_EMPTY


def test_escalate_auto_critical_triggers(ready):
    coord, send, tid = ready
    coord.workspaces["wsp_p"].tasks[tid].routing_hints = {
        "criticality": "critical", "confidence": "0.9",
    }
    r = send("escalate.auto", workspace="wsp_p", task_id=tid,
             default_escalation_target="human:bob@x")
    assert r["result"]["escalate"] is True
    assert r["result"]["to"] == "human:bob@x"


# -------- audit-scitt/1.0 --------

def test_audit_verify_chain(ready):
    coord, send, tid = ready
    r = send("audit.verify_chain", workspace="wsp_p")
    assert r["result"]["ok"] is True
    assert r["result"]["entries_checked"] > 0


def test_audit_submit_to_scitt_without_submitter_returns_statements(ready):
    coord, send, tid = ready
    r = send("audit.submit_to_scitt", workspace="wsp_p",
             **{"from": "service:coordinator"})
    # No scitt_submitter configured - statements returned instead
    assert "statements" in r["result"]
    assert len(r["result"]["statements"]) > 0
    stmt = r["result"]["statements"][0]
    assert stmt["protected"]["content-type"].startswith("application/chap+json")


def test_audit_submit_to_scitt_with_submitter(ready):
    """When a submitter hook is configured, receipts are returned."""
    captured: list[dict] = []

    def submitter(statement: dict) -> dict:
        captured.append(statement)
        return {"receipt_id": f"r-{len(captured)}", "log_root": "abc"}

    coord, send, tid = ready
    coord.options.scitt_submitter = submitter
    r = send("audit.submit_to_scitt", workspace="wsp_p",
             **{"from": "service:coordinator"})
    assert "receipts" in r["result"]
    assert len(r["result"]["receipts"]) > 0
