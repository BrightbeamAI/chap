"""
A review a task requires ends with a decision, and handing the work on does
not make its author a reviewer of it (review/1.0 S3.1, S3.5).

The authors of the work under review could end a required review with no
decision through control.cancel, control.supersede, or control.pause and
then task.update to cancelled. A person who wrote the work could hand it on
with escalate.raise or have it superseded, and then approve the successor as
one of its eligible reviewers; and a later round, resubmitted by someone
else, dropped the earlier round's authors. A request after an approval named
the approving reviewer as an author. Mirrors review_successors.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0"]
ENDS = "The authors of the work under review cannot end its review without a decision"
AUTHOR_DECIDES = "The author of the work under review cannot decide it"


def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES, deterministic_ids=True,
                                       deterministic_clock=True))
    n = [0]

    def send(method, actor="agent:b", **params):
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=PROFILES)
    for uri, kind in (("human:a", "human"), ("human:c", "human"), ("human:h", "human"),
                      ("agent:b", "agent")):
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
