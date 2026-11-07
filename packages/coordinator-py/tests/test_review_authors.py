"""
The authors of the work under review cannot decide it, and a review that
requires a person is decided by one.

On a task that required review the agent's decide.approve was refused, so it
called review.request with the same artefact and ``to: [itself]``; the
coordinator widened the review, and the agent's next approval completed the
task (6 November review, item 1). A broadcast reviewer let any member decide,
the completer included. A task taken back from review with task.update then
completed with no decision at all when it did not require review.

Mirrors packages/coordinator/tests/review_authors.test.ts.
"""
from __future__ import annotations

import copy
import json

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E
from chap_coordinator.storage.sqlite import SqliteStore

DRAFT = {"body": "draft"}
MEMBERS = (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent"), ("agent:d", "agent"))


def _ready(members=MEMBERS, **options):
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "review/1.0"],
                                       deterministic_ids=True, **options))
    n = [0]

    def send(method, actor="agent:b", **params):
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create")
    for uri, kind in members:
        send("participant.join", actor=uri, type=kind)
    return c, send


def _task(c, tid):
    return c.get_workspace("w").tasks[tid]


def _ok(reply):
    assert "error" not in reply, reply.get("error")
    return reply["result"]


def _refused(reply, message):
    assert reply.get("error", {}).get("code") == E.NOT_AUTHORISED, reply
    assert reply["error"]["message"] == message


def _required_under_review():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b",
                   review_required=True))["task_id"]
    assert _ok(send("task.complete", task_id=tid, output=DRAFT))["state"] == "review_requested"
    return c, send, tid


def test_the_agent_cannot_add_itself_or_anyone_to_its_own_required_review_or_approve_it():
    c, send, tid = _required_under_review()
    for to in (["agent:b"], ["human:c"]):
        _refused(send("review.request", task_id=tid, artefact=DRAFT, to=to),
                 "The author of the work under review cannot request its review")
    _refused(send("decide.approve", task_id=tid),
             "The author of the work under review cannot decide it")
    assert _task(c, tid).review.requested_to == ["human:a", "human:c"]
    assert _task(c, tid).state == "review_requested"


#: The refusal of a review.request that would open a required review.
OPENS_ON_COMPLETE = ("This task requires a person's review, which opens on task.complete; "
                     "review.request only adds reviewers to it once it is open")


def test_before_completing_nobody_opens_a_required_review_with_review_request():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b",
                   review_required=True))["task_id"]
    _refused(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]), OPENS_ON_COMPLETE)
    _refused(send("review.request", actor="human:a", task_id=tid, artefact=DRAFT, to=["agent:b"]),
             OPENS_ON_COMPLETE)
    # A person who is not an author cannot open it either, with an artefact
    # they supplied and themselves as its reviewer, and approve it.
    _refused(send("review.request", actor="human:a", task_id=tid, artefact={"by": "a"},
                  to=["human:a"]), OPENS_ON_COMPLETE)
    assert _task(c, tid).review is None
    assert _task(c, tid).state == "created"


def test_a_completed_required_review_cannot_be_reopened_with_review_request():
    c, send, tid = _required_under_review()
    _ok(send("decide.approve", actor="human:a", task_id=tid))
    assert _task(c, tid).state == "completed"
    _refused(send("review.request", actor="human:c", task_id=tid, artefact={"by": "c"},
                  to=["human:c"]), OPENS_ON_COMPLETE)
    assert _task(c, tid).state == "completed"
    assert _task(c, tid).output == DRAFT


def test_another_agent_cannot_be_added_to_a_required_review():
    c, send, tid = _required_under_review()
    _refused(send("review.request", actor="human:a", task_id=tid, artefact=DRAFT, to=["agent:d"]),
             "This task requires a person's review: agent:d is not a human member")


def test_a_required_review_names_its_reviewers_and_a_broadcast_scope_is_refused():
    c, send, tid = _required_under_review()
    for scope in ("group:any", "workspace:w"):
        r = send("review.request", actor="human:a", task_id=tid, artefact=DRAFT, to=[scope])
        assert r["error"] == {
            "code": E.PARAMS,
            "message": f"A review that requires a person names its reviewers, and {scope} "
                       "is an address scope"}
    assert _task(c, tid).review.requested_to == ["human:a", "human:c"]
    _refused(send("decide.approve", actor="agent:d", task_id=tid),
             "This task requires a person's review: agent:d is not a human member")
    assert _ok(send("decide.approve", actor="human:c", task_id=tid))["state"] == "completed"


def test_only_the_assignee_completes_a_task_that_requires_review():
    # Completed by another member, the task would count that member as an
    # author, and routing it to a reviewer first would take that reviewer off
    # the review.
    c, send = _ready()
    tid = _ok(send("task.create", actor="human:c", kind="k", input={}, assignee="human:a",
                   review_required=True))["task_id"]
    _refused(send("task.complete", task_id=tid, output=DRAFT),
             "Only the assignee completes a task that requires review")
    assert _task(c, tid).state == "created"
    assert _ok(send("task.complete", actor="human:a", task_id=tid,
                    output=DRAFT))["state"] == "review_requested"
    assert _task(c, tid).review.authors == ["human:a"]


def test_only_the_works_authors_take_it_back_from_review_and_an_undecided_review_is_not_declined():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]))
    _refused(send("task.update", actor="agent:d", task_id=tid, state="in_progress"),
             "Only the authors of the work under review take it back from review")
    _ok(send("task.update", task_id=tid, state="in_progress"))
    declined = send("task.update", task_id=tid, state="declined")
    assert declined["error"] == {
        "code": E.PARAMS,
        "message": "This task holds a review no decision has closed; a reviewer's decision ends it"}
    assert _task(c, tid).state == "in_progress"
    # Once a reviewer rejected the work, the review is decided, and its
    # author may give the task up.
    _ok(send("task.complete", task_id=tid, output={"body": "v2"}))
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    assert _ok(send("task.update", task_id=tid, state="declined"))["state"] == "declined"


def test_an_escalation_of_a_task_that_held_a_review_requires_review():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]))
    nxt = _ok(send("escalate.raise", original_task_id=tid,
                   new_task={"assignee": "agent:d"}))["new_task_id"]
    assert _task(c, nxt).review_required is True
    assert _ok(send("task.complete", actor="agent:d", task_id=nxt,
                    output=DRAFT))["state"] == "review_requested"


def test_the_completer_cannot_decide_a_broadcast_review_of_its_own_work():
    # The bridges' pattern: complete, then request review of the output.
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", task_id=tid, output=DRAFT))
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["group:any"]))
    for verb in ("decide.approve", "decide.reject"):
        _refused(send(verb, task_id=tid), "The author of the work under review cannot decide it")
    _refused(send("decide.override", task_id=tid, diff=[], rationale="r"),
             "The author of the work under review cannot decide it")
    _refused(send("abstain.declare", task_id=tid, reason="r"),
             "The author of the work under review cannot decide it")
    assert _ok(send("decide.approve", actor="agent:d", task_id=tid))["state"] == "completed"


def test_an_explicit_review_of_a_task_that_does_not_require_one_keeps_its_own_reviewers():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["agent:d"]))
    assert _ok(send("decide.approve", actor="agent:d", task_id=tid))["state"] == "completed"


def test_a_review_records_the_assignee_and_the_completer_as_its_authors():
    c, send = _ready()
    required = _ok(send("task.create", kind="k", input={}, assignee="agent:b",
                        review_required=True))["task_id"]
    _ok(send("task.complete", task_id=required, output=DRAFT))
    assert _task(c, required).review.authors == ["agent:b"]
    assert _task(c, required).review.requested_to == ["human:a", "human:c"]

    explicit = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", actor="agent:d", task_id=explicit, output=DRAFT))
    _ok(send("review.request", task_id=explicit, artefact=DRAFT, to=["human:a"]))
    assert _task(c, explicit).review.authors == ["agent:b", "agent:d"]

    fresh = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=fresh, artefact=DRAFT, to=["human:a"]))
    assert _task(c, fresh).review.authors == ["agent:b"]


def test_a_task_that_holds_a_review_completes_only_through_a_decision():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]))
    _ok(send("task.update", task_id=tid, state="in_progress"))
    r = send("task.update", task_id=tid, state="completed")
    assert r["error"]["code"] == E.PARAMS and "holds a review" in r["error"]["message"]
    assert _ok(send("task.complete", task_id=tid, output={"body": "v2"}))["state"] == "review_requested"
    assert _task(c, tid).review.decisions == []
    assert _task(c, tid).review.requested_to == ["human:a"]
    assert _ok(send("decide.approve", actor="human:a", task_id=tid))["state"] == "completed"
    assert _task(c, tid).output == {"body": "v2"}


def test_a_completion_that_would_leave_no_reviewer_but_its_authors_is_refused():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["agent:b"]))
    _ok(send("task.update", task_id=tid, state="in_progress"))
    _refused(send("task.complete", task_id=tid, output=DRAFT),
             "No reviewer of this task remains once the authors of the work are left out")
    assert _task(c, tid).state == "in_progress"


def test_a_new_round_of_a_required_review_leaves_out_a_reviewer_who_is_not_a_person():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b",
                   review_required=True))["task_id"]
    _ok(send("task.complete", task_id=tid, output=DRAFT))
    # A review written before this release could name an agent.
    _task(c, tid).review.requested_to.append("agent:d")
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    _ok(send("task.complete", task_id=tid, output={"body": "v2"}))
    assert _task(c, tid).review.requested_to == ["human:a", "human:c"]


def test_a_review_restored_without_authors_takes_them_from_the_tasks_history():
    # A task one agent completed for another and then sent for review, and one
    # whose completion opened a review its assignee completed: the shapes a
    # store written before authors were recorded holds.
    from chap_coordinator.storage.store import MemoryStore
    store = MemoryStore()
    c, send = _ready(store=store)
    explicit = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", actor="agent:d", task_id=explicit, output=DRAFT))
    _ok(send("review.request", actor="agent:d", task_id=explicit, artefact=DRAFT,
             to=["group:any"]))
    implicit = _ok(send("task.create", kind="k", input={}, assignee="agent:b",
                        review_required=True))["task_id"]
    _ok(send("task.complete", task_id=implicit, output=DRAFT))
    fresh = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=fresh, artefact=DRAFT, to=["group:any"]))
    # A review a person approved: the approval completed the task, and is not
    # a completion of the work.
    approved = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", actor="agent:d", task_id=approved, output=DRAFT))
    _ok(send("review.request", actor="agent:d", task_id=approved, artefact=DRAFT, to=["human:a"]))
    _ok(send("decide.approve", actor="human:a", task_id=approved))
    # A review another member opened on work it did not do, with an artefact
    # it supplied.
    supplied = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", task_id=supplied, output=DRAFT))
    _ok(send("review.request", actor="agent:d", task_id=supplied, artefact=DRAFT, to=["human:a"]))
    # Rewrite the stored tasks as a release before authors wrote them.
    held = store._held["w"]["objects"]["tasks"]
    for tid in (explicit, implicit, fresh, approved, supplied):
        stored = json.loads(held[tid])
        del stored["review"]["authors"]
        held[tid] = json.dumps(stored)
    again = Coordinator(CoordinatorOptions(store=store))
    tasks = again.workspaces["w"].tasks
    assert tasks[explicit].review.authors == ["agent:b", "agent:d"]
    assert tasks[implicit].review.authors == ["agent:b"]
    assert tasks[fresh].review.authors == ["agent:b"]
    assert tasks[approved].review.authors == ["agent:b", "agent:d"]
    assert tasks[supplied].review.authors == ["agent:b", "agent:d"]
    r = again.dispatch({"jsonrpc": "2.0", "id": "x", "method": "decide.approve",
                        "params": {"workspace": "w", "from": "agent:d", "task_id": explicit}})
    assert r["error"]["code"] == E.NOT_AUTHORISED


def test_a_member_restored_with_a_type_its_uri_does_not_name_takes_the_schemes():
    from chap_coordinator.storage.store import MemoryStore
    store = MemoryStore()
    c, send = _ready(store=store)
    tid = _ok(send("task.create", actor="human:a", kind="k", input={}, assignee="agent:b",
                   review_required=True))["task_id"]
    _ok(send("task.complete", task_id=tid, output=DRAFT))
    # Written by a release that took the type a join declared.
    objects = store._held["w"]["objects"]
    objects["members"]["agent:evil"] = json.dumps({
        "uri": "agent:evil", "type": "human", "role": "admin", "joined": "t", "keys": [],
        "paused": False})
    stored = json.loads(objects["tasks"][tid])
    stored["review"]["requested_to"].append("agent:evil")
    objects["tasks"][tid] = json.dumps(stored)
    again = Coordinator(CoordinatorOptions(store=store))
    member = again.workspaces["w"].members["agent:evil"]
    assert member.type == "agent"
    assert member.role == "participant"
    r = again.dispatch({"jsonrpc": "2.0", "id": "x", "method": "decide.approve",
                        "params": {"workspace": "w", "from": "agent:evil", "task_id": tid}})
    assert r["error"]["message"] == ("This task requires a person's review: agent:evil is not a "
                                     "human member")


def test_authors_are_written_to_the_store_and_a_new_rounds_authors_replace_them(tmp_path):
    path = str(tmp_path / "chap.db")
    store = SqliteStore(path)
    c, send = _ready(members=(("human:a", "human"), ("agent:b", "agent"), ("agent:d", "agent")),
                     store=store)
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("task.complete", task_id=tid, output=DRAFT))
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]))
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    _ok(send("task.complete", actor="agent:d", task_id=tid, output=DRAFT))
    store.close()
    reopened = SqliteStore(path)
    again = Coordinator(CoordinatorOptions(store=reopened))
    assert again.workspaces["w"].tasks[tid].review.authors == ["agent:b", "agent:d"]
    assert (copy.deepcopy(again._snapshot_workspace(again.workspaces["w"]).data["tasks"])
            == c._snapshot_workspace(c.workspaces["w"]).data["tasks"])
    reopened.close()
