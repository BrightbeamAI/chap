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


def test_the_agent_cannot_add_itself_to_its_own_required_review_or_approve_it():
    c, send, tid = _required_under_review()
    _refused(send("review.request", task_id=tid, artefact=DRAFT, to=["agent:b"]),
             "The author of the work under review cannot review it: agent:b")
    _refused(send("decide.approve", task_id=tid),
             "The author of the work under review cannot decide it")
    assert _task(c, tid).review.requested_to == ["human:a", "human:c"]
    assert _task(c, tid).state == "review_requested"


def test_before_completing_the_agent_cannot_open_a_required_review_addressed_to_itself():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b",
                   review_required=True))["task_id"]
    _refused(send("review.request", task_id=tid, artefact=DRAFT, to=["agent:b"]),
             "The author of the work under review cannot review it: agent:b")
    assert _task(c, tid).review is None


def test_another_agent_cannot_be_added_to_a_required_review():
    c, send, tid = _required_under_review()
    _refused(send("review.request", actor="human:a", task_id=tid, artefact=DRAFT, to=["agent:d"]),
             "This task requires a person's review: agent:d is not a human member")


def test_a_broadcast_reviewer_on_a_required_review_still_means_a_person():
    c, send, tid = _required_under_review()
    _ok(send("review.request", actor="human:a", task_id=tid, artefact=DRAFT, to=["group:any"]))
    _refused(send("decide.approve", actor="agent:d", task_id=tid),
             "This task requires a person's review: agent:d is not a human member")
    _refused(send("decide.approve", task_id=tid),
             "The author of the work under review cannot decide it")
    assert _ok(send("decide.approve", actor="human:c", task_id=tid))["state"] == "completed"


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
    _ok(send("task.complete", actor="agent:d", task_id=required, output=DRAFT))
    assert _task(c, required).review.authors == ["agent:b", "agent:d"]
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


def test_a_review_restored_without_authors_takes_its_assignee():
    from chap_coordinator.storage.store import MemoryStore
    store = MemoryStore()
    c, send = _ready(store=store)
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["group:any"]))
    # Rewrite the stored task as a release before authors wrote it.
    held = store._held["w"]["objects"]["tasks"]
    stored = json.loads(held[tid])
    del stored["review"]["authors"]
    held[tid] = json.dumps(stored)
    again = Coordinator(CoordinatorOptions(store=store))
    assert again.workspaces["w"].tasks[tid].review.authors == ["agent:b"]
    r = again.dispatch({"jsonrpc": "2.0", "id": "x", "method": "decide.approve",
                        "params": {"workspace": "w", "from": "agent:b", "task_id": tid}})
    assert r["error"]["code"] == E.NOT_AUTHORISED


def test_authors_are_written_to_the_store_and_a_new_rounds_authors_replace_them(tmp_path):
    path = str(tmp_path / "chap.db")
    store = SqliteStore(path)
    c, send = _ready(members=(("human:a", "human"), ("agent:b", "agent"), ("agent:d", "agent")),
                     store=store)
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b",
                   review_required=True))["task_id"]
    _ok(send("task.complete", task_id=tid, output=DRAFT))
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    _ok(send("task.complete", actor="agent:d", task_id=tid, output=DRAFT))
    store.close()
    reopened = SqliteStore(path)
    again = Coordinator(CoordinatorOptions(store=reopened))
    assert again.workspaces["w"].tasks[tid].review.authors == ["agent:b", "agent:d"]
    assert (copy.deepcopy(again._snapshot_workspace(again.workspaces["w"]).data["tasks"])
            == c._snapshot_workspace(c.workspaces["w"]).data["tasks"])
    reopened.close()
