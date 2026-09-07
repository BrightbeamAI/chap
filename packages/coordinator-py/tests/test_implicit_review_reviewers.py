"""
An implicit review is addressed to people, not to whichever member happens to
be left over.

0.2.12 made `task.complete` open a review when the task requires one, addressed
to the members who are neither the completer nor the assignee. That excludes the
producer, which was the point, but in a workspace with more than one agent it
also makes the *other agent* an eligible reviewer. It could then approve, and
the chain would carry a `decide.approve` that reads as human oversight and is
not, which is the failure `review_required` exists to prevent.

The implicit review now addresses the human members other than the completer and
the assignee. Where there are none the completion is refused rather than opening
a review no person can decide. An explicit `review.request` keeps whatever `to`
it was given, so a deliberate agent-reviews-agent flow is still available.

Mirrored by packages/coordinator/tests/implicit_review_reviewers.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E

PROFILES = ["core/1.0", "review/1.0"]
DRAFT = {"text": "draft"}


def _ws(members, profiles=PROFILES):
    c = Coordinator(CoordinatorOptions(default_profiles=profiles, deterministic_ids=True))

    def send(method, actor="agent:drafter", **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=profiles)
    for uri, kind in members:
        send("participant.join", actor=uri, type=kind)
    return c, send


def _task(c, tid):
    return c.get_workspace("w").tasks[tid]


ONE_HUMAN_TWO_AGENTS = [("human:you", "human"),
                        ("agent:drafter", "agent"),
                        ("agent:other", "agent")]


def test_the_implicit_review_is_addressed_to_the_human():
    c, send = _ws(ONE_HUMAN_TWO_AGENTS)
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    send("task.complete", task_id=tid, output=DRAFT)
    assert _task(c, tid).review.requested_to == ["human:you"]


def test_another_agent_cannot_approve_the_first_agents_work():
    c, send = _ws(ONE_HUMAN_TWO_AGENTS)
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    send("task.complete", task_id=tid, output=DRAFT)

    r = send("decide.approve", actor="agent:other", task_id=tid,
             comment="looks fine", rationale="looks fine")

    assert "error" in r, "an agent approved another agent's work"
    assert r["error"]["code"] == E.NOT_AUTHORISED
    assert _task(c, tid).state == "review_requested"
    assert not _task(c, tid).review.decisions


def test_the_human_can_still_approve():
    c, send = _ws(ONE_HUMAN_TWO_AGENTS)
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    send("task.complete", task_id=tid, output=DRAFT)
    r = send("decide.approve", actor="human:you", task_id=tid, comment="ok", rationale="ok")
    assert r["result"]["state"] == "completed"
    assert _task(c, tid).output == DRAFT


def test_every_human_is_addressed():
    c, send = _ws([("human:you", "human"), ("human:them", "human"),
                   ("agent:drafter", "agent"), ("agent:other", "agent")])
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    send("task.complete", task_id=tid, output=DRAFT)
    assert set(_task(c, tid).review.requested_to) == {"human:you", "human:them"}


def test_the_completer_is_excluded_even_when_human():
    c, send = _ws([("human:you", "human"), ("human:them", "human"),
                   ("agent:drafter", "agent")])
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    send("task.complete", actor="human:you", task_id=tid, output=DRAFT)
    assert _task(c, tid).review.requested_to == ["human:them"]


def test_the_assignee_is_excluded_even_when_human():
    c, send = _ws([("human:you", "human"), ("human:them", "human"),
                   ("agent:drafter", "agent")])
    tid = send("task.create", kind="k", input={}, assignee="human:them",
               review_required=True)["result"]["task_id"]
    send("task.complete", actor="agent:drafter", task_id=tid, output=DRAFT)
    assert _task(c, tid).review.requested_to == ["human:you"]


def test_a_workspace_with_no_human_refuses_the_completion():
    # Fail closed. Opening a review no person can decide is worse than refusing:
    # it produces an audit trail that looks supervised.
    c, send = _ws([("agent:drafter", "agent"), ("agent:other", "agent")])
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    before = len(send("audit.read")["result"]["entries"])

    r = send("task.complete", task_id=tid, output=DRAFT)

    assert "error" in r
    assert r["error"]["code"] == E.NOT_AUTHORISED
    assert "human" in r["error"]["message"]
    task = _task(c, tid)
    assert task.state == "created", "the refused completion still moved the task"
    assert task.output is None, "unreviewed output was written anyway"
    assert task.review is None
    assert len(send("audit.read")["result"]["entries"]) == before


def test_the_only_human_cannot_review_their_own_work():
    # The same exclusion with the producer a person rather than an agent: the
    # agent in the workspace does not become the reviewer by default.
    c, send = _ws([("human:you", "human"), ("agent:drafter", "agent")])
    tid = send("task.create", actor="human:you", kind="k", input={},
               assignee="human:you", review_required=True)["result"]["task_id"]
    r = send("task.complete", actor="human:you", task_id=tid, output=DRAFT)
    assert "error" in r and r["error"]["code"] == E.NOT_AUTHORISED
    assert _task(c, tid).state == "created"


def test_a_service_member_is_not_a_reviewer():
    c, send = _ws([("service:coordinator", "service"), ("agent:drafter", "agent")])
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    r = send("task.complete", task_id=tid, output=DRAFT)
    assert "error" in r and r["error"]["code"] == E.NOT_AUTHORISED
    assert _task(c, tid).state == "created"


def test_an_explicit_request_can_still_address_an_agent():
    # Agent-reviews-agent stays available when somebody asks for it on purpose.
    c, send = _ws(ONE_HUMAN_TWO_AGENTS)
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter")["result"]["task_id"]
    send("review.request", task_id=tid, artefact=DRAFT, to=["agent:other"])
    assert _task(c, tid).review.requested_to == ["agent:other"]
    r = send("decide.approve", actor="agent:other", task_id=tid, comment="ok", rationale="ok")
    assert r["result"]["state"] == "completed"


def test_an_explicit_request_before_completion_is_left_alone():
    # task.complete only computes a reviewer set when there is no review yet.
    c, send = _ws(ONE_HUMAN_TWO_AGENTS)
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter",
               review_required=True)["result"]["task_id"]
    send("review.request", task_id=tid, artefact=DRAFT, to=["agent:other"])
    send("task.complete", task_id=tid, output=DRAFT)
    assert _task(c, tid).review.requested_to == ["agent:other"]


def test_trial_mode_takes_the_same_path():
    # modes/1.0 forces review by setting review_required, so the same rule
    # applies to a trial workspace that never mentioned review_required itself.
    profiles = ["core/1.0", "review/1.0", "modes/1.0"]
    c, send = _ws(ONE_HUMAN_TWO_AGENTS, profiles=profiles)
    tid = send("task.create", kind="k", input={}, assignee="agent:drafter")["result"]["task_id"]
    assert _task(c, tid).review_required is True, "trial mode should force review"
    send("task.complete", task_id=tid, output=DRAFT)
    assert _task(c, tid).review.requested_to == ["human:you"]
