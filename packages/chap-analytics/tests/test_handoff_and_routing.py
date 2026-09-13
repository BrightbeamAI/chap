"""
Handoffs, routing, and the columns that used to lie.

Every test here corresponds to a defect the first version shipped with. They
are kept separate from test_projection.py so the reason each exists stays
legible: this file is the record of what went wrong.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

from chap_analytics import frames, from_coordinator  # noqa: E402
from chap_analytics.load import Chain  # noqa: E402

PROFILES = ["core/1.0", "review/1.0", "handoff/1.0", "routing/1.0",
            "whisper/1.0", "control/1.0"]


def build():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))
    send = lambda m, p=None, a="human:ana": c.dispatch({  # noqa: E731
        "jsonrpc": "2.0", "id": m, "method": m,
        "params": {"workspace": "w", "from": a, **(p or {})}})
    send("workspace.create", {"profiles": PROFILES})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"),
                      ("agent:x", "agent"), ("agent:y", "agent")]:
        send("participant.join", {"type": kind}, uri)

    def ok(m, p=None, a="human:ana"):
        r = send(m, p, a)
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})
    return c, ok


def both(c):
    """The same workspace read each way, which is where disagreements hide."""
    entries = c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                          "params": {"workspace": "w", "from": "human:ana"}})["result"]["entries"]
    return (frames(Chain(workspace="w", events=entries, state=None, source="audit.read")),
            frames(from_coordinator(c, workspace="w")))


# ------------------------------------------------------------------ handoffs

def test_a_handed_off_task_is_not_lost_from_an_envelope_only_read():
    # handoff.propose names its tasks in a nested list rather than as a
    # top-level task_id. Reading only the top level dropped them entirely.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    h = ok("handoff.propose", {"to": "human:bo", "tasks": [{"task_id": t}]}, "agent:x")["handoff_id"]
    ok("handoff.accept", {"handoff_id": h}, "human:bo")

    env, state = both(c)
    for label, f in (("envelopes", env), ("state", state)):
        assert t in set(f.tasks["task_id"]), f"{label}: the handed-off task vanished"


def test_accepting_a_handoff_moves_the_assignee_in_both_reads():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    h = ok("handoff.propose", {"to": "human:bo", "tasks": [{"task_id": t}]}, "agent:x")["handoff_id"]
    ok("handoff.accept", {"handoff_id": h}, "human:bo")

    truth = c.get_workspace("w").tasks[t].assignee
    assert truth == "human:bo"
    for label, f in zip(("envelopes", "state"), both(c)):
        got = f.tasks.set_index("task_id").loc[t, "assignee"]
        assert got == truth, f"{label} read says {got}, the coordinator says {truth}"


def test_a_declined_handoff_leaves_the_work_where_it_was():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    h = ok("handoff.propose", {"to": "human:bo", "tasks": [{"task_id": t}]}, "agent:x")["handoff_id"]
    ok("handoff.decline", {"handoff_id": h, "reason": "Not my area."}, "human:bo")

    env, _ = both(c)
    assert env.tasks.set_index("task_id").loc[t, "assignee"] == "agent:x"
    row = env.handoffs.iloc[0]
    assert row["resolution"] == "declined"
    assert row["reason"] == "Not my area."
    assert row["n_accepted"] == 0


def test_one_member_declining_does_not_close_an_offer_made_to_a_group():
    # A group recipient is an offer to whoever is free, and the coordinator
    # keeps it proposed until someone takes it or the named recipient turns it
    # down. Resolving it on the first decline reported a decline rate of one on
    # work that was still on offer, in both reads, with no way to see it.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    h = ok("handoff.propose", {"to": "group:oncall", "tasks": [{"task_id": t}]},
           "agent:x")["handoff_id"]
    ok("handoff.decline", {"handoff_id": h, "reason": "On holiday."}, "human:bo")

    assert c.get_workspace("w").handoffs[h].state == "proposed"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.handoffs.iloc[0]
        assert row["resolution"] == "open", f"{label} read"
        assert row["reason"] == "On holiday.", "the decline is still worth recording"
        import pandas as pd
        assert pd.isna(row["resolved_at"]) and pd.isna(row["resolved_by"])
        assert pd.isna(row["response_s"])


def test_a_partial_acceptance_counts_only_what_was_taken():
    c, ok = build()
    a = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    b = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    h = ok("handoff.propose",
           {"to": "human:bo", "tasks": [{"task_id": a}, {"task_id": b}]}, "agent:x")["handoff_id"]
    ok("handoff.accept", {"handoff_id": h, "accepted_task_ids": [a]}, "human:bo")

    env, _ = both(c)
    row = env.handoffs.iloc[0]
    assert (row["n_tasks"], row["n_accepted"]) == (2, 1)
    tasks = env.tasks.set_index("task_id")
    assert tasks.loc[a, "assignee"] == "human:bo"
    assert tasks.loc[b, "assignee"] == "agent:x", "an unaccepted task stays where it was"


def test_an_outstanding_handoff_is_open_rather_than_missing():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("handoff.propose", {"to": "human:bo", "tasks": [{"task_id": t}]}, "agent:x")

    for f in both(c):
        assert len(f.handoffs) == 1
        assert f.handoffs.iloc[0]["resolution"] == "open"


# ------------------------------------------------------------------- routing

def test_routing_outcomes_are_populated_when_state_carries_them():
    # The whole routing table was previously null in every state column, which
    # made it useless for the one question it exists to answer.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    chosen = ok("task.route", {"task_id": t, "candidates": ["agent:y", "agent:x"]})["selected"]

    _, state = both(c)
    row = state.routing.iloc[0]
    assert row["method"] == "task.route"
    assert row["selected"] == chosen
    assert row["policy_id"] is not None
    assert row["n_candidates"] == 2
    assert row["n_alternatives"] >= 1, "the candidates passed over should be counted"


def test_a_stale_routing_choice_does_not_overwrite_a_later_handoff():
    # The routing artefact records who the policy chose at the time. Writing it
    # onto the task as well put that choice back over an assignee a handoff had
    # since moved, and only on the read that was supposed to be authoritative.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("task.route", {"task_id": t, "candidates": ["agent:y", "agent:x"]})
    h = ok("handoff.propose", {"to": "human:bo", "tasks": [{"task_id": t}]},
           c.get_workspace("w").tasks[t].assignee)["handoff_id"]
    ok("handoff.accept", {"handoff_id": h}, "human:bo")

    assert c.get_workspace("w").tasks[t].assignee == "human:bo"
    _, state = both(c)
    row = state.tasks.set_index("task_id").loc[t]
    assert row["assignee"] == "human:bo", "the work moved after routing chose"
    assert bool(row["assignee_certain"]) is True
    assert state.routing.iloc[0]["selected"] == "agent:y", (
        "the routing decision is still recorded as the decision it was")


def test_an_acceptance_that_could_have_been_either_offer_says_so_on_both_tasks():
    # Two offers to the same person, one accepted. The log leaves open which
    # offer the acceptance belongs to, so one task is reported as moved while
    # it stayed put and another moved while the table leaves it where it
    # started. Both are named by an offer whose identity is in doubt, and both
    # have to say so.
    c, ok = build()
    a = ok("task.create", {"kind": "a", "input": {}, "assignee": "agent:x"})["task_id"]
    b = ok("task.create", {"kind": "b", "input": {}, "assignee": "agent:y"})["task_id"]
    ok("handoff.propose", {"to": "human:bo", "tasks": [{"task_id": a}]}, "agent:x")
    second = ok("handoff.propose", {"to": "human:bo", "tasks": [{"task_id": b}]},
                "agent:y")["handoff_id"]
    ok("handoff.accept", {"handoff_id": second}, "human:bo")

    env, state = both(c)
    assert not env.handoffs["id_certain"].any(), "two identical offers are interchangeable"
    rows = env.tasks.set_index("task_id")
    for tid in (a, b):
        assert not bool(rows.loc[tid, "assignee_certain"]), (
            "an assignee moved by an offer of uncertain identity is an inference")
    # State settles it, and says so.
    stated = state.tasks.set_index("task_id")
    assert stated.loc[b, "assignee"] == "human:bo"
    assert stated.loc[a, "assignee"] == "agent:x"
    assert bool(stated["assignee_certain"].all())


def test_a_routing_reassignment_is_admitted_as_unknown_without_state():
    # task.route returns its choice in the result, so the envelopes hold the
    # pre-routing assignee alone. The row keeps that value and flags it.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    chosen = ok("task.route", {"task_id": t, "candidates": ["agent:y", "agent:x"]})["selected"]
    assert chosen == "agent:y"

    env, state = both(c)
    e = env.tasks.set_index("task_id").loc[t]
    s = state.tasks.set_index("task_id").loc[t]

    assert s["assignee"] == chosen and bool(s["assignee_certain"]) is True
    assert bool(e["assignee_certain"]) is False, (
        "an envelope-only read sees the pre-routing assignee alone, and has to say so")


def test_review_depth_and_auto_escalation_record_their_verdicts():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x",
                           "routing_hints": {"criticality": "critical"}})["task_id"]
    ok("review.depth", {"task_id": t})
    ok("escalate.auto", {"task_id": t, "default_escalation_target": "human:bo"})

    _, state = both(c)
    by_method = state.routing.set_index("method")
    assert by_method.loc["review.depth", "depth"] == "full"
    assert bool(by_method.loc["escalate.auto", "escalated"]) is True


# -------------------------------------------------------------- the whisper lapse

def test_a_whisper_answered_after_its_deadline_still_lapsed():
    # `answered` and `lapsed` are separate questions. The default was already
    # applied at the deadline, and a late answer leaves that in place.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    w = ok("whisper.ask", {"task_id": t, "to": ["human:ana"], "question": "q?",
                           "deadline_ms": 0, "default_if_lapsed": "no"}, "agent:x")["whisper_id"]
    ok("whisper.answer", {"whisper_id": w, "answer": "yes"}, "human:ana")

    env, _ = both(c)
    row = env.whispers.iloc[0]
    assert bool(row["answered"]) is True
    assert bool(row["lapsed"]) is True, (
        "a zero-millisecond deadline had already passed when the answer arrived")


def test_a_whisper_answered_in_time_did_not_lapse():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    w = ok("whisper.ask", {"task_id": t, "to": ["human:ana"], "question": "q?",
                           "deadline_ms": 600_000, "default_if_lapsed": "no"},
           "agent:x")["whisper_id"]
    ok("whisper.answer", {"whisper_id": w, "answer": "yes"}, "human:ana")

    env, _ = both(c)
    row = env.whispers.iloc[0]
    assert bool(row["answered"]) is True and bool(row["lapsed"]) is False


# ------------------------------------------------- repeated corrections

def test_two_overrides_on_one_task_keep_their_own_artefacts():
    # Matching stored artefacts on (task, reviewer) attached the first one to
    # both, so the second override reported the wrong "before".
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    v1 = {"body": "one"}
    ok("task.complete", {"task_id": t, "output": v1}, "agent:x")
    ok("review.request", {"task_id": t, "artefact": v1, "to": ["human:ana"]}, "agent:x")
    ok("decide.override", {"task_id": t, "diff": [{"op": "replace", "path": "/body", "value": "two"}],
                           "rationale": "first correction"}, "human:ana")
    v2 = {"body": "two"}
    ok("review.request", {"task_id": t, "artefact": v2, "to": ["human:ana"]}, "agent:x")
    ok("decide.override", {"task_id": t, "diff": [{"op": "replace", "path": "/body", "value": "three"}],
                           "rationale": "second correction"}, "human:ana")

    _, state = both(c)
    ovs = state.overrides.sort_values("seq")
    assert len(ovs) == 2
    assert ovs.iloc[0]["based_on"] == v1
    assert ovs.iloc[1]["based_on"] == v2, (
        "the second correction started from the corrected artefact rather than the first draft")


# ------------------------------------------------------------- degenerate input

def test_an_empty_workspace_projects_to_empty_tables_not_an_exception():
    c, ok = build()
    for f in both(c):
        assert len(f.tasks) == 0 and len(f.decisions) == 0
        assert len(f.participants) == 4
        # An empty frame carries the declared columns and dtypes, so downstream
        # code works the same on a quiet day.
        from chap_analytics import TABLES
        for table in TABLES:
            assert list(f[table.name].columns) == table.names


def test_a_chain_with_no_events_at_all_is_projectable():
    empty = Chain(workspace="w", events=[], state=None, source="test")
    f = frames(empty)
    from chap_analytics import TABLES
    for table in TABLES:
        assert len(f[table.name]) == 0
        assert list(f[table.name].columns) == table.names
