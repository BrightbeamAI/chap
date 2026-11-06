"""
review.acknowledge: a reviewer says they have begun the open review
(review/1.0 S3.6).

The method was in the catalogue as spec-only, so a reviewer opening a review
left nothing on the log, and a reader could not tell a review nobody had
looked at from one in progress.

Mirrors packages/coordinator/tests/review_acknowledge.test.ts.
"""
from __future__ import annotations

import json

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E
from chap_coordinator.storage.sqlite import SqliteStore
from chap_coordinator.storage.store import MemoryStore

DRAFT = {"body": "draft"}
MEMBERS = (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent"), ("agent:d", "agent"))


def _ready(**options):
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0", "review/1.0"],
                                       deterministic_ids=True, deterministic_clock=True,
                                       **options))
    n = [0]

    def send(method, actor="agent:b", **params):
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create")
    for uri, kind in MEMBERS:
        send("participant.join", actor=uri, type=kind)
    return c, send


def _task(c, tid):
    return c.workspaces["w"].tasks[tid]


def _log(c):
    return len(c.workspaces["w"].audit)


def _ok(reply):
    assert "error" not in reply, reply.get("error")
    return reply["result"]


def _refused(reply, code, message):
    assert reply.get("error", {}).get("code") == code, reply
    assert reply["error"]["message"] == message


def _under_review(to=("human:a", "human:c"), required=False):
    """A task of agent:b's under an explicit review addressed to ``to``."""
    c, send = _ready()
    extra = {"review_required": True} if required else {}
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b", **extra))["task_id"]
    if required:
        _ok(send("task.complete", task_id=tid, output=DRAFT))
    else:
        _ok(send("review.request", task_id=tid, artefact=DRAFT, to=list(to)))
    return c, send, tid


def test_a_reviewers_acknowledgement_is_recorded_names_the_round_and_decides_nothing():
    c, send, tid = _under_review()
    before = _log(c)
    result = _ok(send("review.acknowledge", actor="human:a", task_id=tid))
    review = _task(c, tid).review
    assert result == {"acknowledged": True, "requested_at": review.requested_at}
    assert _log(c) == before + 1
    assert c.workspaces["w"].audit[-1].envelope["method"] == "review.acknowledge"
    assert [a["reviewer"] for a in review.acknowledgements] == ["human:a"]
    assert isinstance(review.acknowledgements[0]["ts"], str)
    assert _task(c, tid).state == "review_requested"
    assert review.decisions == []


def test_a_reviewer_acknowledges_a_round_once_and_the_refusal_is_not_recorded():
    c, send, tid = _under_review()
    _ok(send("review.acknowledge", actor="human:a", task_id=tid))
    before = _log(c)
    _refused(send("review.acknowledge", actor="human:a", task_id=tid),
             E.PARAMS, "Already acknowledged in this round")
    assert _log(c) == before
    _ok(send("review.acknowledge", actor="human:c", task_id=tid))
    assert [a["reviewer"] for a in _task(c, tid).review.acknowledgements] == ["human:a", "human:c"]


def test_a_task_with_no_open_review_cannot_be_acknowledged():
    c, send = _ready()
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _refused(send("review.acknowledge", actor="human:a", task_id=tid),
             E.NOT_REVIEWABLE, "Task not awaiting review: created")
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]))
    _ok(send("decide.approve", actor="human:a", task_id=tid))
    _refused(send("review.acknowledge", actor="human:a", task_id=tid),
             E.NOT_REVIEWABLE, "Task not awaiting review: completed")


def test_only_someone_who_may_decide_the_review_may_acknowledge_it():
    c, send, tid = _under_review(to=("human:a",))
    before = _log(c)
    _refused(send("review.acknowledge", task_id=tid),
             E.NOT_AUTHORISED, "The author of the work under review cannot decide it")
    _refused(send("review.acknowledge", actor="human:c", task_id=tid),
             E.NOT_AUTHORISED, "Not an addressed reviewer for this task: human:c")
    _refused(send("review.acknowledge", actor="human:z", task_id=tid),
             E.NOT_AUTHORISED, "Not a workspace member: human:z")
    # The members' refusals are recorded; the outsider's is not (SPECIFICATION 10.1).
    assert _log(c) == before + 2
    assert _task(c, tid).review.acknowledgements == []


def test_an_author_cannot_acknowledge_a_broadcast_review_of_its_own_work():
    c, send, tid = _under_review(to=("workspace:w",))
    _refused(send("review.acknowledge", task_id=tid),
             E.NOT_AUTHORISED, "The author of the work under review cannot decide it")
    _ok(send("review.acknowledge", actor="agent:d", task_id=tid))


def test_a_review_that_requires_a_person_is_acknowledged_by_a_person():
    c, send, tid = _under_review(to=(), required=True)
    _refused(send("review.acknowledge", actor="agent:d", task_id=tid),
             E.NOT_AUTHORISED, "This task requires a person's review: agent:d is not a human member")
    _ok(send("review.acknowledge", actor="human:c", task_id=tid))


def test_a_new_round_starts_with_no_acknowledgements_and_an_amendment_keeps_them():
    c, send, tid = _under_review(to=("human:a",))
    first = _ok(send("review.acknowledge", actor="human:a", task_id=tid))["requested_at"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:c"]))
    assert [a["reviewer"] for a in _task(c, tid).review.acknowledgements] == ["human:a"]
    _ok(send("decide.reject", actor="human:a", task_id=tid, request_revision=True))
    _ok(send("task.complete", task_id=tid, output={"body": "second draft"}))
    assert _task(c, tid).review.acknowledgements == []
    second = _ok(send("review.acknowledge", actor="human:a", task_id=tid))["requested_at"]
    assert second != first
    assert second == _task(c, tid).review.requested_at


def test_acknowledgements_are_kept_by_the_store_and_a_review_stored_without_them_has_none(tmp_path):
    path = str(tmp_path / "chap.db")
    store = SqliteStore(path)
    c, send = _ready(store=store)
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]))
    _ok(send("review.acknowledge", actor="human:a", task_id=tid))
    store.close()
    reopened = SqliteStore(path)
    again = Coordinator(CoordinatorOptions(store=reopened))
    assert _task(again, tid).review.acknowledgements == _task(c, tid).review.acknowledgements
    reopened.close()

    memory = MemoryStore()
    c, send = _ready(store=memory)
    tid = _ok(send("task.create", kind="k", input={}, assignee="agent:b"))["task_id"]
    _ok(send("review.request", task_id=tid, artefact=DRAFT, to=["human:a"]))
    # Rewrite the stored task as a release before acknowledgements wrote it.
    held = memory._held["w"]["objects"]["tasks"]
    stored = json.loads(held[tid])
    del stored["review"]["acknowledgements"]
    held[tid] = json.dumps(stored)
    restored = Coordinator(CoordinatorOptions(store=memory))
    assert _task(restored, tid).review.acknowledgements == []
    r = restored.dispatch({"jsonrpc": "2.0", "id": "x", "method": "review.acknowledge",
                           "params": {"workspace": "w", "from": "human:a", "task_id": tid}})
    assert r["result"]["acknowledged"] is True


def test_review_acknowledge_belongs_to_review():
    c = Coordinator(CoordinatorOptions(default_profiles=["core/1.0"]))
    c.dispatch({"jsonrpc": "2.0", "id": "w", "method": "workspace.create",
                "params": {"workspace": "w", "from": "human:a"}})
    r = c.dispatch({"jsonrpc": "2.0", "id": "x", "method": "review.acknowledge",
                    "params": {"workspace": "w", "from": "human:a", "task_id": "t"}})
    assert r["error"]["code"] == E.METHOD
    assert r["error"]["data"]["profile"] == "review/1.0"
