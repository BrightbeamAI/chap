"""
A review a task requires ends with a decision, and handing the work on does
not make its author a reviewer of it (review/1.0 S3.1, S3.5).

The authors of the work under review could end a required review with no
decision through control.cancel, control.supersede, or control.pause and
then task.update to cancelled. A person who wrote the work could hand it on
with escalate.raise, task.route or handoff.accept, or have it superseded,
and then approve the work as one of its eligible reviewers; and a later
round, resubmitted by someone
else, dropped the earlier round's authors. A request after an approval named
the approving reviewer as an author. Mirrors review_successors.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0", "routing/1.0"]
ENDS = "The authors of the work under review cannot end its review without a decision"
AUTHOR_DECIDES = "The author of the work under review cannot decide it"


def _ready(rule=None):
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES, deterministic_ids=True,
                                       deterministic_clock=True))
    n = [0]

    def send(method, actor="agent:b", **params):
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=PROFILES, **({"review_rule": rule} if rule else {}))
    for uri, kind in (("human:a", "human"), ("human:c", "human"), ("human:h", "human"),
                      ("agent:b", "agent"), ("agent:d", "agent")):
        send("participant.join", actor=uri, type=kind)
    return c, send


def _ok(reply):
    assert "error" not in reply, reply.get("error")
    return reply["result"]


def _task(c, tid):
    return c.get_workspace("w").tasks[tid]


def _under_review(send, assignee="agent:b"):
    tid = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee=assignee,
                   review_required=True))["task_id"]
    _ok(send("task.complete", actor=assignee, task_id=tid, output={"body": "v1"}))
    return tid


def test_the_authors_cannot_cancel_or_supersede_a_required_review_or_cancel_it_once_paused():
    c, send = _ready()
    tid = _under_review(send)
    for method, params in (("control.cancel", {"task_id": tid}),
                           ("control.supersede", {"task_id": tid, "successor_task": {"kind": "k"}})):
        assert send(method, **params)["error"] == {"code": E.NOT_AUTHORISED, "message": ENDS}, method
    _ok(send("control.pause", task_id=tid))
    assert send("task.update", task_id=tid, state="cancelled")["error"] == {
        "code": E.NOT_AUTHORISED, "message": ENDS}
    assert _task(c, tid).state == "paused"
    # Another member still pulls the brake.
    _ok(send("task.update", actor="human:a", task_id=tid, state="cancelled"))
    assert _task(c, tid).state == "cancelled"


def test_authors_who_take_the_work_back_still_cannot_end_its_review_until_a_reviewer_rejects_it():
    c, send = _ready()
    tid = _under_review(send)
    _ok(send("task.update", task_id=tid, state="in_progress"))
    for method, params in (("control.cancel", {"task_id": tid}),
                           ("control.supersede", {"task_id": tid, "successor_task": {"kind": "k"}})):
        assert send(method, **params)["error"] == {"code": E.NOT_AUTHORISED, "message": ENDS}, method
    _ok(send("control.pause", task_id=tid))
    assert send("task.update", task_id=tid, state="cancelled")["error"] == {
        "code": E.NOT_AUTHORISED, "message": ENDS}
    _ok(send("control.resume", actor="human:a", task_id=tid))
    _ok(send("task.complete", task_id=tid, output={"body": "v2"}))
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    _ok(send("control.cancel", task_id=tid))
    assert _task(c, tid).state == "cancelled"


def test_a_person_a_task_passes_through_who_never_acts_on_it_stays_among_its_reviewers():
    c, send = _ready("all_approve")
    routed = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="agent:b",
                      review_required=True))["task_id"]
    _ok(send("task.route", task_id=routed, candidates=["human:c"]))
    _ok(send("task.route", task_id=routed, candidates=["agent:b"]))
    assert _task(c, routed).prior_authors == ["agent:b"]
    _ok(send("task.complete", task_id=routed, output={"body": "v1"}))
    assert _task(c, routed).review.requested_to == ["human:a", "human:c", "human:h"]
    first = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="agent:b",
                     review_required=True))["task_id"]
    second = _ok(send("escalate.raise", original_task_id=first,
                      new_task={"kind": "k", "assignee": "human:c"}))["new_task_id"]
    third = _ok(send("escalate.raise", original_task_id=second,
                     new_task={"kind": "k", "assignee": "agent:b"}))["new_task_id"]
    assert _task(c, third).prior_authors == ["agent:b"]
    _ok(send("task.complete", task_id=third, output={"body": "v1"}))
    assert _task(c, third).review.requested_to == ["human:a", "human:c", "human:h"]


def test_the_person_who_created_a_task_or_rejected_a_round_stays_a_reviewer_when_it_passes_through_them():
    c, send = _ready()
    tid = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="agent:b",
                   review_required=True))["task_id"]
    _ok(send("task.route", task_id=tid, candidates=["human:a"]))
    _ok(send("task.route", task_id=tid, candidates=["agent:b"]))
    _ok(send("task.complete", task_id=tid, output={"body": "v1"}))
    assert _task(c, tid).review.requested_to == ["human:a", "human:c", "human:h"]
    _ok(send("decide.reject", actor="human:c", task_id=tid, request_revision=True))
    _ok(send("task.route", task_id=tid, candidates=["human:c"]))
    _ok(send("task.route", task_id=tid, candidates=["agent:b"]))
    _ok(send("task.complete", task_id=tid, output={"body": "v2"}))
    assert "human:c" in _task(c, tid).review.requested_to
    assert _ok(send("decide.approve", actor="human:c", task_id=tid))["state"] == "completed"


def test_an_author_who_escalates_a_required_review_cannot_end_the_successor_and_its_assignee_can():
    c, send = _ready()
    tid = _under_review(send)
    successor = _ok(send("escalate.raise", original_task_id=tid,
                         new_task={"kind": "k", "assignee": "agent:d"}))["new_task_id"]
    assert send("control.cancel", task_id=successor)["error"] == {"code": E.NOT_AUTHORISED, "message": ENDS}
    assert send("task.update", task_id=successor, state="declined")["error"] == {
        "code": E.PARAMS,
        "message": "This task carries on a review no decision has closed; completing it opens the "
                   "review a reviewer decides"}
    _ok(send("control.cancel", actor="agent:d", task_id=successor))
    assert _task(c, successor).state == "cancelled"


def test_a_chain_of_escalations_however_long_holds_the_authors_and_a_decided_review_holds_nobody():
    c, send = _ready()
    tid = _under_review(send)
    for _ in range(70):
        tid = _ok(send("escalate.raise", original_task_id=tid,
                       new_task={"kind": "k", "assignee": "agent:b"}))["new_task_id"]
    assert send("control.cancel", task_id=tid)["error"] == {"code": E.NOT_AUTHORISED, "message": ENDS}
    approved = _under_review(send)
    _ok(send("decide.approve", actor="human:a", task_id=approved))
    successor = _ok(send("control.supersede", actor="human:a", task_id=approved,
                         successor_task={"kind": "k", "assignee": "agent:b"}))["new_task_id"]
    _ok(send("control.cancel", task_id=successor))
    assert _task(c, successor).state == "cancelled"


def test_a_route_of_a_completed_task_or_a_note_an_update_carries_does_not_hide_who_completed_it():
    c, send = _ready()
    send("participant.join", actor="agent:x", type="agent")
    routed = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", actor="agent:x", task_id=routed, output={"body": "v1"}))
    _ok(send("task.route", actor="human:a", task_id=routed, candidates=["agent:b"]))
    noted = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.update", actor="agent:x", task_id=noted, state="in_progress"))
    _ok(send("task.update", actor="agent:x", task_id=noted, state="completed",
             progress_note="override applied"))
    for tid in (routed, noted):
        _ok(send("review.request", actor="human:a", task_id=tid, artefact={"body": "v1"},
                 to=["agent:x"]))
        assert send("decide.approve", actor="agent:x", task_id=tid)["error"] == {
            "code": E.NOT_AUTHORISED, "message": AUTHOR_DECIDES}


def test_a_member_who_is_not_an_author_still_cancels_or_supersedes_a_required_review():
    c, send = _ready()
    first = _under_review(send)
    _ok(send("control.cancel", actor="human:a", task_id=first))
    assert _task(c, first).state == "cancelled"
    second = _under_review(send)
    r = _ok(send("control.supersede", actor="human:a", task_id=second, successor_task={"kind": "k"}))
    assert _task(c, r["new_task_id"]).review_required is True


def test_the_person_who_wrote_the_work_cannot_approve_it_after_escalating_it():
    c, send = _ready()
    tid = _under_review(send, "human:h")
    new_id = _ok(send("escalate.raise", actor="human:h", original_task_id=tid,
                      new_task={"kind": "k", "assignee": "agent:b"}))["new_task_id"]
    assert _task(c, new_id).prior_authors == ["human:h"]
    _ok(send("task.complete", task_id=new_id, output={"body": "v1"}))
    assert _task(c, new_id).review.authors == ["agent:b", "human:h"]
    assert "human:h" not in _task(c, new_id).review.requested_to
    assert send("decide.approve", actor="human:h", task_id=new_id)["error"] == {
        "code": E.NOT_AUTHORISED, "message": AUTHOR_DECIDES}
    assert _ok(send("decide.approve", actor="human:a", task_id=new_id))["state"] == "completed"


def test_the_person_who_held_the_work_cannot_approve_it_after_routing_it_or_handing_it_off():
    c, send = _ready()
    routed = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="human:h",
                      review_required=True))["task_id"]
    _ok(send("task.route", actor="human:h", task_id=routed, candidates=["agent:b"]))
    handed = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="human:h",
                      review_required=True))["task_id"]
    handoff_id = _ok(send("handoff.propose", actor="human:h", to="agent:b",
                          tasks=[{"task_id": handed, "summary": "yours"}]))["handoff_id"]
    _ok(send("handoff.accept", handoff_id=handoff_id))
    for tid in (routed, handed):
        assert _task(c, tid).prior_authors == ["human:h"]
        _ok(send("task.complete", task_id=tid, output={"body": "v1"}))
        assert _task(c, tid).review.authors == ["agent:b", "human:h"]
        assert send("decide.approve", actor="human:h", task_id=tid)["error"] == {
            "code": E.NOT_AUTHORISED, "message": AUTHOR_DECIDES}
        assert _ok(send("decide.approve", actor="human:a", task_id=tid))["state"] == "completed"


def test_the_person_who_wrote_the_work_cannot_approve_its_successor_after_a_supersede():
    c, send = _ready()
    tid = _under_review(send, "human:h")
    new_id = _ok(send("control.supersede", actor="human:a", task_id=tid,
                      successor_task={"kind": "k", "assignee": "agent:b"}))["new_task_id"]
    _ok(send("task.complete", task_id=new_id, output={"body": "v1"}))
    assert send("decide.approve", actor="human:h", task_id=new_id)["error"] == {
        "code": E.NOT_AUTHORISED, "message": AUTHOR_DECIDES}


def test_the_authors_of_an_earlier_round_stay_authors_when_someone_else_resubmits():
    c, send = _ready()
    tid = _under_review(send, "human:h")
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    handoff = _ok(send("handoff.propose", actor="human:h", to="agent:b",
                       tasks=[{"task_id": tid}]))["handoff_id"]
    _ok(send("handoff.accept", handoff_id=handoff))
    _ok(send("task.complete", task_id=tid, output={"body": "v2"}))
    assert _task(c, tid).review.authors == ["human:h", "agent:b"]
    assert send("review.request", actor="human:a", task_id=tid, artefact={"body": "v2"},
                to=["human:h"])["error"] == {
        "code": E.NOT_AUTHORISED,
        "message": "The author of the work under review cannot review it: human:h"}
    assert send("decide.approve", actor="human:h", task_id=tid)["error"]["code"] == E.NOT_AUTHORISED


def test_a_request_after_an_approval_does_not_name_the_approving_reviewer_an_author():
    c, send = _ready()
    tid = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", task_id=tid, output={"body": "v1"}))
    _ok(send("review.request", actor="human:a", task_id=tid, artefact={"body": "v1"}, to=["human:c"]))
    assert _ok(send("decide.approve", actor="human:c", task_id=tid))["state"] == "completed"
    _ok(send("review.request", actor="human:a", task_id=tid, artefact={"body": "v1"},
             to=["human:c", "human:h"]))
    assert _task(c, tid).review.authors == ["agent:b"]
    assert _ok(send("decide.approve", actor="human:c", task_id=tid))["state"] == "completed"


def test_a_paused_participant_does_not_lift_its_own_pause_and_another_member_does():
    c, send = _ready()
    _ok(send("control.pause", actor="human:a", scope="participant", participant_uri="agent:b"))
    assert send("control.resume", scope="participant", participant_uri="agent:b")["error"] == {
        "code": E.NOT_AUTHORISED, "message": "A paused participant does not lift its own pause"}
    assert c.workspaces["w"].members["agent:b"].paused is True
    assert send("participant.leave")["error"] == {
        "code": E.NOT_AUTHORISED,
        "message": "A paused participant does not leave until another member lifts its pause"}, \
        "leaving and joining again would lift it"
    _ok(send("control.resume", actor="human:a", scope="participant", participant_uri="agent:b"))
    assert c.workspaces["w"].members["agent:b"].paused is False
