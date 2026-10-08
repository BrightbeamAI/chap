"""Who may be given work, and who may move it.

escalate.raise creates a task, so it meets the checks task.create and
control.supersede apply: the successor keeps the review requirement of the
original, a trial successor requires review under modes/1.0, a paused
assignee is refused and so is a mode above the current ceiling.
control.supersede keeps the review requirement too. The routing methods and
participant.leave are held to the membership floor (SPECIFICATION 6.3.1). A
paused participant is assigned no task through task.route, escalate.raise or
handoff.accept.

Mirrors packages/coordinator/tests/assignment_guards.test.ts.
"""
from __future__ import annotations

import itertools

from chap_coordinator import Coordinator, CoordinatorOptions

WS = "wsp_guard"


def setup(profiles, **options):
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, **options,
    ))
    counter = itertools.count(1)

    def send(method, params):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"g-{next(counter)}",
                               "method": method, "params": params})

    send("workspace.create", {"workspace": WS,
                              "profiles": ["core/1.0", "review/1.0", *profiles]})
    for uri, type_, role in (
        ("human:alice", "human", "admin"),
        ("human:bob", "human", "reviewer"),
        ("agent:bot", "agent", "drafter"),
        ("agent:senior", "agent", "drafter"),
    ):
        send("participant.join", {"workspace": WS, "from": uri,
                                  "type": type_, "role": role})
    return coord, send, coord.workspaces[WS]


def create_task(send, **extra):
    r = send("task.create", {"workspace": WS, "from": "human:alice", "kind": "k",
                             "input": {}, "assignee": "agent:bot", **extra})
    assert "result" in r, f"task.create failed: {r.get('error')}"
    tid = r["result"]["task_id"]
    send("task.update", {"workspace": WS, "from": "agent:bot", "task_id": tid,
                         "state": "in_progress"})
    return tid


def escalate(send, original, assignee="agent:senior", **extra):
    return send("escalate.raise", {
        "workspace": WS, "from": "agent:bot", "original_task_id": original,
        "new_task": {"kind": "k", "assignee": assignee, "input": {}, **extra},
    })


def complete_successor(send, successor):
    send("task.update", {"workspace": WS, "from": "agent:senior",
                         "task_id": successor, "state": "in_progress"})
    done = send("task.complete", {"workspace": WS, "from": "agent:senior",
                                  "task_id": successor, "output": {"d": 1}})
    assert "result" in done, f"task.complete failed: {done.get('error')}"
    return done["result"]["state"]


def pause(send, uri):
    r = send("control.pause", {"workspace": WS, "from": "human:alice",
                               "scope": "participant", "participant_uri": uri})
    assert "result" in r, f"control.pause failed: {r.get('error')}"


# -------- escalate.raise --------

def test_escalate_raise_keeps_the_review_the_original_required():
    coord, send, ws = setup([])
    original = create_task(send, review_required=True)

    r = escalate(send, original)
    successor = r["result"]["new_task_id"]

    assert ws.tasks[successor].review_required is True
    assert complete_successor(send, successor) == "review_requested", \
        "the successor of a reviewed task must not complete directly"


def test_escalate_raise_forces_review_on_a_trial_successor_under_modes():
    # The original is a trial task made before the workspace advertised
    # modes/1.0, so it carries no review. Escalating it once the profile is on
    # makes a trial task, which requires review as one made by task.create
    # does.
    coord, send, ws = setup(["control/1.0"])
    original = create_task(send, mode="trial")
    assert ws.tasks[original].review_required is None
    set_ = send("workspace.set_profiles", {
        "workspace": WS, "from": "human:alice",
        "profiles": ["core/1.0", "review/1.0", "control/1.0", "modes/1.0"],
    })
    assert "result" in set_, f"workspace.set_profiles failed: {set_.get('error')}"

    r = escalate(send, original)
    successor = r["result"]["new_task_id"]

    assert ws.tasks[successor].mode == "trial"
    assert ws.tasks[successor].review_required is True
    assert complete_successor(send, successor) == "review_requested"


def test_escalate_raise_leaves_review_off_when_neither_rule_applies():
    # Without modes/1.0 a trial mode is inert, as at task.create.
    coord, send, ws = setup([])
    original = create_task(send, mode="trial")

    r = escalate(send, original)
    successor = r["result"]["new_task_id"]

    assert ws.tasks[successor].review_required is None
    assert complete_successor(send, successor) == "completed"


def test_escalate_raise_refuses_a_paused_assignee():
    coord, send, ws = setup(["control/1.0"])
    original = create_task(send)
    pause(send, "agent:senior")
    tasks_before = len(ws.tasks)

    r = escalate(send, original)

    assert r["error"]["code"] == -32063
    assert r["error"]["message"] == "Assignee agent:senior is paused"
    assert len(ws.tasks) == tasks_before
    assert ws.tasks[original].state == "in_progress"
    # A member's refused call is recorded as a refusal (SPECIFICATION 10.1).
    assert ws.audit[-1].outcome == {"status": "refused", "code": -32063}


def test_escalate_raise_refuses_a_mode_above_the_current_ceiling():
    coord, send, ws = setup(["control/1.0"])
    original = create_task(send, mode="production")
    lowered = send("control.set_mode_ceiling", {"workspace": WS, "from": "human:alice",
                                                "new_ceiling": "trial"})
    assert "result" in lowered, f"control.set_mode_ceiling failed: {lowered.get('error')}"
    tasks_before = len(ws.tasks)

    r = escalate(send, original)

    assert r["error"]["code"] == -32040
    assert r["error"]["message"] == "Requested mode production exceeds ceiling trial"
    assert len(ws.tasks) == tasks_before
    assert ws.tasks[original].state == "in_progress"


def test_escalate_raise_takes_a_mode_within_a_lowered_ceiling_from_new_task():
    coord, send, ws = setup(["control/1.0"])
    original = create_task(send, mode="production")
    send("control.set_mode_ceiling", {"workspace": WS, "from": "human:alice",
                                      "new_ceiling": "trial"})

    above = escalate(send, original, mode="production")
    assert above["error"]["code"] == -32040
    malformed = escalate(send, original, mode=3)
    assert malformed["error"]["code"] == -32602
    assert malformed["error"]["message"] == "new_task.mode must be a string"

    r = escalate(send, original, mode="trial")
    successor = r["result"]["new_task_id"]
    assert ws.tasks[successor].mode == "trial"
    assert ws.tasks[original].state == "escalated"


def test_control_supersede_keeps_the_review_requirement_of_the_task_it_replaces():
    # The successor's own review_required: false cannot remove it.
    coord, send, ws = setup(["control/1.0"])
    original = create_task(send, review_required=True)

    r = send("control.supersede", {
        "workspace": WS, "from": "agent:bot", "task_id": original, "reason": "redo",
        "successor_task": {"kind": "v2", "assignee": "agent:senior", "input": {},
                           "review_required": False},
    })
    successor = r["result"]["new_task_id"]

    assert ws.tasks[successor].review_required is True
    assert complete_successor(send, successor) == "review_requested"


def test_control_supersede_still_lets_a_successor_of_an_unreviewed_task_opt_out():
    coord, send, ws = setup(["control/1.0"])
    original = create_task(send)

    r = send("control.supersede", {
        "workspace": WS, "from": "human:alice", "task_id": original, "reason": "redo",
        "successor_task": {"kind": "v2", "assignee": "agent:senior", "input": {},
                           "review_required": False},
    })
    successor = r["result"]["new_task_id"]

    assert ws.tasks[successor].review_required is False
    assert complete_successor(send, successor) == "completed"


# -------- membership --------

ROUTING_CALLS = [
    ("task.route", {"candidates": ["human:bob"]}),
    ("review.depth", {}),
    ("escalate.auto", {"default_escalation_target": "human:bob"}),
]


def test_a_non_member_cannot_call_the_routing_methods():
    coord, send, ws = setup(["routing/1.0"])
    tid = create_task(send, routing_hints={"criticality": "critical"})

    for method, extra in ROUTING_CALLS:
        audit_before = len(ws.audit)
        decisions_before = len(ws.route_decisions)
        r = send(method, {"workspace": WS, "from": "human:outsider",
                          "task_id": tid, **extra})
        assert r["error"]["code"] == -32011, f"{method} from a non-member"
        # A non-member's refusal is never recorded.
        assert len(ws.audit) == audit_before, f"{method} wrote to the log"
        assert len(ws.route_decisions) == decisions_before, f"{method} recorded a decision"
        assert ws.tasks[tid].assignee == "agent:bot", f"{method} moved the task"

    # The same calls from a member succeed.
    for method, extra in ROUTING_CALLS:
        r = send(method, {"workspace": WS, "from": "human:alice",
                          "task_id": tid, **extra})
        assert "result" in r, f"{method} from a member failed: {r.get('error')}"
    assert ws.tasks[tid].assignee == "human:bob"


def test_a_non_member_cannot_write_a_leave_to_the_log():
    coord, send, ws = setup([])
    audit_before = len(ws.audit)

    outsider = send("participant.leave", {"workspace": WS, "from": "human:outsider"})
    assert outsider["error"]["code"] == -32011
    assert len(ws.audit) == audit_before

    member = send("participant.leave", {"workspace": WS, "from": "agent:senior"})
    assert member["result"] == {"left": True}
    assert "agent:senior" not in ws.members
    assert len(ws.audit) == audit_before + 1

    again = send("participant.leave", {"workspace": WS, "from": "agent:senior"})
    assert again["error"]["code"] == -32011
    assert len(ws.audit) == audit_before + 1


_ABSENT = object()


def test_a_from_that_is_not_a_string_is_refused_in_fixed_words():
    coord, send, ws = setup(["routing/1.0"])
    tid = create_task(send)
    audit_before = len(ws.audit)

    for from_ in (_ABSENT, None, ["human:alice"], {"uri": "human:alice"}, 7, True):
        for method, extra in (("participant.leave", {}),
                              ("task.route", {"task_id": tid, "candidates": ["human:bob"]})):
            params = {"workspace": WS, **extra}
            if from_ is not _ABSENT:
                params["from"] = from_
            r = send(method, params)
            assert r["error"]["code"] == -32011, f"{method} from {from_!r}"
            assert r["error"]["message"] == "Not a workspace member: from is not a participant URI"
    assert len(ws.audit) == audit_before
    assert len(ws.members) == 4


def test_a_from_or_workspace_that_is_not_a_string_is_refused_under_signatures_and_step_up():
    for options in ({"require_signatures": True}, {"enforce_step_up": True}):
        coord = Coordinator(CoordinatorOptions(
            deterministic_ids=True, deterministic_clock=True, **options))
        signed = options.get("require_signatures", False)

        def send(method, params, sig=None):
            env = {"jsonrpc": "2.0", "id": method, "method": method, "params": params}
            if sig:
                env["sig"] = sig
            return coord.dispatch(env)

        send("workspace.create", {"workspace": WS, "profiles": ["core/1.0", "control/1.0"]})
        send("participant.join", {"workspace": WS, "from": "human:alice",
                                  "type": "human", "role": "admin"})
        sig = "ed25519:k1:AAAA" if signed else None
        expected = ((-32070, "Cannot verify signature: missing from/workspace") if signed
                    else (-32011, "Not a workspace member: from is not a participant URI"))
        cases = [{"workspace": WS, "from": ["human:alice"]},
                 {"workspace": WS, "from": {"uri": "human:alice"}}]
        if signed:
            cases.append({"workspace": [WS], "from": "human:alice"})
        for params in cases:
            method = "participant.leave" if signed else "control.pause"
            r = send(method, {**params, "scope": "workspace"}, sig)
            assert r["error"]["code"] == expected[0], f"{options} {params}"
            assert r["error"]["message"] == expected[1]
        assert coord.workspaces[WS].state == "active"
        assert len(coord.workspaces[WS].members) == 1


def test_malformed_fields_are_refused_in_the_same_words_by_both_references():
    coord, send, ws = setup(["control/1.0", "routing/1.0"])
    tid = create_task(send)
    cases = [
        ("task.create", {"kind": "k", "input": {}, "assignee": "agent:bot", "review_required": []},
         "review_required must be a boolean"),
        ("task.create", {"kind": "k", "input": {}, "assignee": "agent:bot", "review_required": "yes"},
         "review_required must be a boolean"),
        ("task.create", {"kind": "k", "input": {}, "assignee": "agent:bot", "mode": {"m": 1}},
         "mode must be a string"),
        ("task.create", {"kind": "k", "input": {}, "assignee": ["agent:bot"]},
         "Assignee not in workspace"),
        ("control.supersede", {"task_id": tid, "successor_task": {"kind": "v2", "review_required": {}}},
         "successor_task.review_required must be a boolean"),
        ("control.supersede", {"task_id": tid, "successor_task": {"kind": "v2", "mode": ["trial"]}},
         "successor_task.mode must be a string"),
        ("control.supersede", {"task_id": tid, "successor_task": 5},
         "successor_task must include kind"),
        ("task.route", {"task_id": tid, "candidates": {"human:bob": 1}}, "candidates must be a list"),
        ("task.route", {"task_id": tid, "candidates": "human:bob"}, "candidates must be a list"),
        ("escalate.raise", {"original_task_id": tid, "new_task": "agent:senior"},
         "new_task must be an object"),
        ("escalate.raise", {"original_task_id": [tid], "new_task": {"assignee": "agent:senior"}},
         "Unknown original task"),
        ("escalate.raise", {"original_task_id": tid, "new_task": {"assignee": ["agent:senior"]}},
         "Escalation assignee not in workspace"),
    ]
    for method, extra, message in cases:
        r = send(method, {"workspace": WS, "from": "human:alice", **extra})
        assert r["error"]["code"] == -32602, f"{method} {extra}"
        assert r["error"]["message"] == message, f"{method} {extra}"
    assert len(ws.tasks) == 1
    assert ws.tasks[tid].state == "in_progress"
    assert ws.tasks[tid].assignee == "agent:bot"


def test_an_empty_successor_mode_falls_back_to_the_originals_in_both_successor_methods():
    coord, send, ws = setup(["control/1.0"])
    first = create_task(send, mode="shadow")
    r1 = send("control.supersede", {
        "workspace": WS, "from": "human:alice", "task_id": first, "reason": "redo",
        "successor_task": {"kind": "v2", "assignee": "agent:senior", "input": {}, "mode": ""},
    })
    assert ws.tasks[r1["result"]["new_task_id"]].mode == "shadow"
    second = create_task(send, mode="shadow")
    r2 = escalate(send, second, mode="")
    assert ws.tasks[r2["result"]["new_task_id"]].mode == "shadow"


# -------- participant pause --------

def test_task_route_passes_over_a_paused_candidate():
    coord, send, ws = setup(["routing/1.0", "control/1.0"])
    tid = create_task(send)
    pause(send, "human:bob")

    r = send("task.route", {"workspace": WS, "from": "human:alice", "task_id": tid,
                            "candidates": ["human:bob", "human:alice"]})

    assert r["result"]["selected"] == "human:alice"
    assert r["result"]["rationale"]["alternatives_considered"] == [
        {"candidate": "human:bob", "reason_excluded": "paused"}]
    assert ws.tasks[tid].assignee == "human:alice"


def test_task_route_with_no_unpaused_member_among_the_candidates_answers_32510():
    coord, send, ws = setup(["routing/1.0", "control/1.0"])
    tid = create_task(send)
    pause(send, "human:bob")

    r = send("task.route", {"workspace": WS, "from": "human:alice", "task_id": tid,
                            "candidates": ["human:bob", "human:ghost"]})

    assert r["error"]["code"] == -32510
    assert r["error"]["message"] == "No candidate is a workspace member who is not paused"
    assert ws.tasks[tid].assignee == "agent:bot"


def test_task_route_refuses_a_paused_participant_an_operator_policy_picks():
    def always_bob(task, candidates):
        return {"selected": "human:bob",
                "rationale": {"policy_id": "always-bob", "summary": "bob"}}

    coord, send, ws = setup(["routing/1.0", "control/1.0"], routing_policy=always_bob)
    tid = create_task(send)
    pause(send, "human:bob")
    decisions_before = len(ws.route_decisions)

    r = send("task.route", {"workspace": WS, "from": "human:alice", "task_id": tid,
                            "candidates": ["human:bob", "human:alice"]})

    assert r["error"]["code"] == -32063
    assert r["error"]["message"] == "Assignee human:bob is paused"
    assert ws.tasks[tid].assignee == "agent:bot"
    assert len(ws.route_decisions) == decisions_before


def test_handoff_accept_refuses_a_paused_recipient_until_it_is_resumed():
    coord, send, ws = setup(["handoff/1.0", "control/1.0"])
    tid = create_task(send)
    proposed = send("handoff.propose", {"workspace": WS, "from": "agent:bot",
                                        "to": "human:bob", "tasks": [{"task_id": tid}]})
    hid = proposed["result"]["handoff_id"]
    pause(send, "human:bob")

    refused = send("handoff.accept", {"workspace": WS, "from": "human:bob",
                                      "handoff_id": hid})
    assert refused["error"]["code"] == -32063
    assert refused["error"]["message"] == "Acceptor human:bob is paused"
    assert ws.handoffs[hid].state == "proposed"
    assert ws.tasks[tid].assignee == "agent:bot"

    send("control.resume", {"workspace": WS, "from": "human:alice",
                            "scope": "participant", "participant_uri": "human:bob"})
    accepted = send("handoff.accept", {"workspace": WS, "from": "human:bob",
                                       "handoff_id": hid})
    assert "result" in accepted, f"handoff.accept failed: {accepted.get('error')}"
    assert ws.tasks[tid].assignee == "human:bob"


def test_handoff_accept_refuses_a_paused_member_of_a_group_recipient():
    coord, send, ws = setup(["handoff/1.0", "control/1.0"])
    tid = create_task(send)
    proposed = send("handoff.propose", {"workspace": WS, "from": "agent:bot",
                                        "to": "group:reviewers",
                                        "tasks": [{"task_id": tid}]})
    hid = proposed["result"]["handoff_id"]
    pause(send, "human:bob")

    refused = send("handoff.accept", {"workspace": WS, "from": "human:bob",
                                      "handoff_id": hid})
    assert refused["error"]["code"] == -32063

    accepted = send("handoff.accept", {"workspace": WS, "from": "human:alice",
                                       "handoff_id": hid})
    assert "result" in accepted, f"handoff.accept failed: {accepted.get('error')}"
    assert ws.tasks[tid].assignee == "human:alice"
