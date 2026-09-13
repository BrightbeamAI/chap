"""
Which row is which.

Server-minted ids are returned in the result and the log records envelopes, so
a creation has to be paired with its id after the fact. The pairing was an
inference presented as a reading: two tasks created in the same millisecond had
their criticality and confidence swapped by the random part of a ULID, and an
escalation between two creations shifted every attribute one row down and
invented a provenance link. These tests fix the pairing where evidence settles
it and require the table to admit the guess where nothing does.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

from chap_analytics import frames, from_coordinator  # noqa: E402
from chap_analytics.load import Chain  # noqa: E402

PROFILES = ["core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0", "control/1.0"]


def build():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def send(m, p=None, a="human:ana"):
        return c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,
                           "params": {"workspace": "w", "from": a, **(p or {})}})

    send("workspace.create", {"profiles": PROFILES})
    for uri, kind in [("human:ana", "human"), ("human:bo", "human"),
                      ("human:cy", "human"), ("agent:x", "agent")]:
        send("participant.join", {"type": kind}, uri)

    def ok(m, p=None, a="human:ana"):
        r = send(m, p, a)
        assert "error" not in r, f"{m}: {r.get('error')}"
        return r.get("result", {})
    return c, ok


def both(c):
    entries = c.dispatch({"jsonrpc": "2.0", "id": "r", "method": "audit.read",
                          "params": {"workspace": "w", "from": "human:ana"}})["result"]["entries"]
    return (frames(Chain(workspace="w", events=entries, state=None, source="audit.read")),
            frames(from_coordinator(c, workspace="w")))


# -------------------------------------------------- pairing against state

@pytest.mark.parametrize("trial", range(40))
def test_tasks_created_in_the_same_millisecond_keep_their_own_attributes(trial):
    # Ordering by created_at then by id sorts the tie by the random suffix of a
    # ULID, so this misattributed roughly half the time and nothing downstream
    # could see it. The creations differ in what they were created as, and that
    # is what settles it.
    c, ok = build()
    low = ok("task.create", {"kind": "LOW", "input": {}, "assignee": "agent:x",
                             "routing_hints": {"criticality": "low",
                                               "confidence": "0.99"}})["task_id"]
    high = ok("task.create", {"kind": "HIGH", "input": {}, "assignee": "agent:x",
                              "routing_hints": {"criticality": "critical",
                                                "confidence": "0.10"}})["task_id"]
    _, f = both(c)
    rows = f.tasks.set_index("task_id")
    assert rows.loc[low, "kind"] == "LOW"
    assert rows.loc[low, "criticality"] == "low"
    assert rows.loc[low, "confidence"] == pytest.approx(0.99)
    assert rows.loc[high, "kind"] == "HIGH"
    assert rows.loc[high, "criticality"] == "critical"
    assert rows.loc[high, "confidence"] == pytest.approx(0.10)


def test_an_escalation_successor_is_not_confused_with_the_next_task_created():
    # A creation that cannot have produced a task with a supersedes link is
    # evidence about which stored task it is, as much as a kind that matches.
    c, ok = build()
    a = ok("task.create", {"kind": "alpha", "input": {}, "assignee": "agent:x"})["task_id"]
    successor = ok("escalate.raise", {
        "original_task_id": a,
        "new_task": {"kind": "gamma", "assignee": "human:cy", "input": {}}})["new_task_id"]
    b = ok("task.create", {"kind": "beta", "input": {}, "assignee": "agent:x"})["task_id"]

    _, f = both(c)
    rows = f.tasks.set_index("task_id")
    assert rows.loc[a, "kind"] == "alpha"
    assert rows.loc[b, "kind"] == "beta"
    assert rows.loc[successor, "kind"] == "gamma"
    assert rows.loc[successor, "supersedes"] == a
    import pandas as pd
    assert pd.isna(rows.loc[b, "supersedes"]), (
        "a task created on its own must not be given another task's provenance")


# ------------------------------------------- admitting what cannot be known

def test_sequential_work_pairs_its_ids_beyond_doubt():
    c, ok = build()
    ids = []
    for i in range(4):
        t = ok("task.create", {"kind": f"k{i}", "input": {}, "assignee": "agent:x"})["task_id"]
        ok("task.update", {"task_id": t, "state": "in_progress"}, "agent:x")
        ids.append(t)

    env, _ = both(c)
    rows = env.tasks.set_index("task_id")
    assert bool(rows["id_certain"].all()), (
        "each id was seen before the next task existed, so nothing else could own it")
    assert [rows.loc[t, "kind"] for t in ids] == ["k0", "k1", "k2", "k3"]


def test_interleaved_work_is_marked_as_a_guess_rather_than_reported_as_fact():
    c, ok = build()
    ids = [ok("task.create", {"kind": f"k{i}", "input": {},
                              "assignee": "agent:x"})["task_id"] for i in range(3)]
    for t in reversed(ids):
        ok("task.update", {"task_id": t, "state": "in_progress"}, "agent:x")

    env, state = both(c)
    assert not env.tasks["id_certain"].any(), (
        "nothing in the log says which id belongs to which creation here")
    # Counts still reconcile: an uncertain row is still a row.
    assert len(env.tasks) == len(state.tasks) == 3
    assert "guess" not in state.summary()
    assert "could not be paired" in env.summary(), (
        "a caveat an analyst has to read the README to learn is a caveat they will miss")


def test_an_escalation_leaves_the_envelope_only_read_unable_to_be_sure():
    # The escalation names the task it supersedes, so that one is still pinned:
    # a break in the ordering makes everything after it a guess and leaves what
    # came before it settled.
    c, ok = build()
    a = ok("task.create", {"kind": "alpha", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("escalate.raise", {"original_task_id": a,
                          "new_task": {"kind": "gamma", "assignee": "human:cy", "input": {}}})
    ok("task.create", {"kind": "beta", "input": {}, "assignee": "agent:x"})

    env, _ = both(c)
    rows = env.tasks.set_index("task_id")
    assert len(rows) == 3, "the successor is still counted, it is just not identified"
    assert bool(rows.loc[a, "id_certain"]), "the escalated task named itself in the log"
    assert rows.loc[a, "kind"] == "alpha"
    assert (~env.tasks["id_certain"]).sum() == 2, (
        "the successor and the task created after it are interchangeable here")


# ----------------------------------------------------- the other id-bearers

def test_a_lapse_is_attributed_to_the_whisper_whose_deadline_had_passed():
    # notify.message is the only record of a lapse in the envelope stream, and
    # taking the oldest outstanding whisper for it swapped a question with a
    # day to run against one with no deadline at all.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "plenty of time",
                       "deadline_ms": 86_400_000, "default_if_lapsed": "no"}, "agent:x")
    ok("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "no time at all",
                       "deadline_ms": 0, "default_if_lapsed": "no"}, "agent:x")
    c.check_whisper_lapses("w")

    for label, f in zip(("envelopes", "state"), both(c)):
        rows = f.whispers.set_index("question")
        assert rows.loc["no time at all", "lapsed"], f"{label} read"
        assert not rows.loc["plenty of time", "lapsed"], (
            f"{label} read moved the lapse onto a question that is still open")
        assert rows.loc["no time at all", "state"] == "lapsed"
        assert rows.loc["plenty of time", "state"] == "pending"


def test_an_open_question_has_not_lapsed():
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "q?",
                       "deadline_ms": 86_400_000, "default_if_lapsed": "no"}, "agent:x")

    assert next(iter(c.get_workspace("w").whispers.values())).state == "pending"
    for label, f in zip(("envelopes", "state"), both(c)):
        row = f.whispers.iloc[0]
        assert not bool(row["lapsed"]), (
            f"{label}: unanswered is not lapsed, and reading it as one reports "
            "every workspace with open questions as one where nobody replies")
        assert row["state"] == "pending"


def test_an_id_the_caller_supplied_is_read_from_the_opening_envelope():
    # The profiles let a client name its own whisper, handoff and deliberation
    # ids, and the coordinator honours them. There is then nothing to infer,
    # however the later envelopes are ordered.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("whisper.ask", {"whisper_id": "wsp-A", "task_id": t, "to": ["human:bo"],
                       "question": "first?", "deadline_ms": 600_000,
                       "default_if_lapsed": "no"}, "agent:x")
    ok("whisper.ask", {"whisper_id": "wsp-B", "task_id": t, "to": ["human:bo"],
                       "question": "second?", "deadline_ms": 600_000,
                       "default_if_lapsed": "no"}, "agent:x")
    ok("whisper.answer", {"whisper_id": "wsp-B", "answer": "yes"}, "human:bo")
    ok("whisper.answer", {"whisper_id": "wsp-A", "answer": "no"}, "human:bo")

    for label, f in zip(("envelopes", "state"), both(c)):
        rows = f.whispers.set_index("whisper_id")
        assert rows.loc["wsp-A", "question"] == "first?", f"{label} read"
        assert rows.loc["wsp-B", "question"] == "second?", f"{label} read"
        assert rows.loc["wsp-A", "answer"] == "no"
        assert bool(rows["id_certain"].all()), (
            f"{label}: an id that was in the envelope is not an inference")


def test_a_whisper_answer_is_attributed_to_a_whisper_its_author_was_asked():
    # The coordinator refuses an answer from anyone the whisper was not
    # addressed to, so an accepted answer names a whisper addressed to its
    # author. Two questions to two people, answered out of order, must not
    # swap.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("whisper.ask", {"task_id": t, "to": ["human:ana"], "question": "for ana",
                       "deadline_ms": 600_000, "default_if_lapsed": "no"}, "agent:x")
    ok("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "for bo",
                       "deadline_ms": 600_000, "default_if_lapsed": "no"}, "agent:x")
    ids = {w.question: w.id for w in c.get_workspace("w").whispers.values()}
    ok("whisper.answer", {"whisper_id": ids["for bo"], "answer": "bo says yes"}, "human:bo")
    ok("whisper.answer", {"whisper_id": ids["for ana"], "answer": "ana says no"}, "human:ana")

    env, _ = both(c)
    rows = env.whispers.set_index("whisper_id")
    assert rows.loc[ids["for bo"], "question"] == "for bo"
    assert rows.loc[ids["for bo"], "answer"] == "bo says yes"
    assert rows.loc[ids["for ana"], "question"] == "for ana"
    assert bool(rows["id_certain"].all()), "each answer could only be about one whisper"


def test_a_successor_inherits_the_doubt_about_what_it_inherited():
    # task.route moves a task to a candidate named only in the result, so the
    # envelope-only read marks the assignee uncertain. A successor that takes
    # its assignee from that task is no surer of it than the task was.
    c, ok = build()
    t = ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x"})["task_id"]
    ok("task.route", {"task_id": t, "candidates": ["agent:x", "human:cy"]})
    s = ok("control.supersede", {"task_id": t, "reason": "Redo.",
                                 "successor_task": {"kind": "v2", "input": {}}})["new_task_id"]
    # Named in a later envelope, so the envelope-only read knows its id.
    ok("task.update", {"task_id": s, "state": "in_progress"},
       c.get_workspace("w").tasks[s].assignee)

    env, state = both(c)
    assert not bool(env.tasks.set_index("task_id").loc[s, "assignee_certain"]), (
        "inherited from a task whose assignee the log could not name")
    stated = state.tasks.set_index("task_id")
    assert stated.loc[s, "assignee"] == c.get_workspace("w").tasks[s].assignee
    assert bool(stated.loc[s, "assignee_certain"])


def test_a_sender_timestamp_in_params_is_the_one_that_is_read():
    # Every profile example stamps ts into params and the coordinator reads it
    # from there first. Reading only the envelope-level field put the
    # coordinator's clock on every row for such a client.
    c, ok = build()
    stamp = "2026-03-01T10:00:00.000Z"
    ok("task.create", {"kind": "k", "input": {}, "assignee": "agent:x", "ts": stamp})

    for label, f in zip(("envelopes", "state"), both(c)):
        created = f.tasks.iloc[0]["created_at"]
        assert str(created) == "2026-03-01 10:00:00+00:00", f"{label} read: {created}"
        assert str(f.events.iloc[-1]["ts"]) == "2026-03-01 10:00:00+00:00"


def test_deliberations_opened_together_do_not_swap_their_questions():
    c, ok = build()
    first = ok("deliberate.open", {"to": ["human:ana", "human:bo", "human:cy"],
                                   "rule": "all_approve",
                                   "question": "Ship the hotfix?"}, "human:ana")["deliberation_id"]
    second = ok("deliberate.open", {"to": ["human:bo"], "rule": "any_one_approves",
                                    "question": "Order pizza?"}, "human:bo")["deliberation_id"]
    ok("deliberate.vote", {"deliberation_id": second, "vote": "yea"}, "human:bo")
    ok("deliberate.vote", {"deliberation_id": first, "vote": "yea"}, "human:ana")

    _, f = both(c)
    rows = f.deliberations.set_index("deliberation_id")
    assert rows.loc[first, "question"] == "Ship the hotfix?"
    assert rows.loc[first, "opener"] == "human:ana"
    assert rows.loc[first, "n_participants"] == 3
    assert rows.loc[second, "question"] == "Order pizza?"
    assert rows.loc[second, "opener"] == "human:bo", (
        "opener and opened_at were left to the replay even where state could say")
    assert rows.loc[second, "n_participants"] == 1
