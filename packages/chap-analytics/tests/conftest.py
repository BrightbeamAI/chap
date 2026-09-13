"""
A realistic workspace, built by driving the real coordinator.

Fixtures are generated rather than checked in as JSON. A hand-written fixture
records what someone believed the coordinator does; a generated one records
what it does. When the protocol changes, a generated fixture changes with it
and the tests fail where the projection has fallen behind.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pandas", reason="chap-analytics is a pandas package")
coordinator = pytest.importorskip(
    "chap_coordinator", reason="the fixtures drive a real coordinator")

from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

PROFILES = [
    "core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0",
    "handoff/1.0", "control/1.0", "routing/1.0", "audit-scitt/1.0",
]

HUMANS = ["human:ana", "human:bo", "human:cy"]
AGENTS = ["agent:drafter", "agent:reviewer-bot"]


class Driver:
    """A thin caller that raises on refusal, so a broken fixture fails loudly."""

    def __init__(self, workspace: str = "wsp_analytics"):
        self.workspace = workspace
        self.coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES))

    def __call__(self, method: str, params: dict | None = None, actor: str = "human:ana"):
        res = self.coord.dispatch({
            "jsonrpc": "2.0", "id": method, "method": method,
            "params": {"workspace": self.workspace, "from": actor, **(params or {})},
        })
        if "error" in res:
            raise AssertionError(
                f"fixture built an invalid call: {method} -> "
                f"{res['error']['code']} {res['error']['message']}")
        return res.get("result", {})


@pytest.fixture(scope="session")
def driver() -> Driver:
    """
    A workspace exercising every table the projection produces.

    Deliberately mixed: approvals, an override, a rejection sent back, an
    abstention, a multi-reviewer quorum, an escalation, a cancellation, an
    unanswered whisper, a closed deliberation, and a task created and left
    alone. Each one is a row some projection has to get right.
    """
    d = Driver()
    d("workspace.create", {"profiles": PROFILES})
    for uri in HUMANS:
        d("participant.join", {"type": "human"}, actor=uri)
    for uri in AGENTS:
        d("participant.join", {"type": "agent"}, actor=uri)

    def make(kind="draft_response", assignee="agent:drafter", **kw) -> str:
        return d("task.create", {"kind": kind, "input": {"ticket": "T-1"},
                                 "assignee": assignee, **kw})["task_id"]

    # 1. Straight approval, with a confidence the agent reported.
    t = make(routing_hints={"criticality": "low", "confidence": "0.93"})
    d("task.update", {"task_id": t, "state": "in_progress"}, actor="agent:drafter")
    d("task.complete", {"task_id": t, "output": {"body": "Refund issued."},
                        "confidence": "0.93"}, actor="agent:drafter")
    d("review.request", {"task_id": t, "artefact": {"body": "Refund issued."},
                         "to": ["human:ana"]}, actor="agent:drafter")
    d("decide.approve", {"task_id": t, "comment": "Correct."}, actor="human:ana")

    # 2. Overridden, the supervision signal this whole package exists for.
    t = make("code_review", routing_hints={"criticality": "high", "confidence": "0.71"})
    draft = {"comments": [{"path": "src/pay.ts", "severity": "warning", "body": "Cast."}]}
    d("task.complete", {"task_id": t, "output": draft, "confidence": "0.71"},
      actor="agent:drafter")
    d("review.request", {"task_id": t, "artefact": draft, "to": ["human:bo"]},
      actor="agent:drafter")
    d("decide.override", {
        "task_id": t,
        "diff": [{"op": "replace", "path": "/comments/0/severity", "value": "info"},
                 {"op": "add", "path": "/comments/0/note", "value": "Framework idiom."}],
        "rationale": "False positive; framework convention, not a bug.",
        "tags": ["false-positive", "framework-pattern-misread"],
        "policy_refs": ["policy:review-severity"],
        "intent_preserved": True,
    }, actor="human:bo")

    # 3. Rejected and sent back, then approved on the second pass.
    t = make()
    d("task.complete", {"task_id": t, "output": {"body": "v1"}}, actor="agent:drafter")
    d("review.request", {"task_id": t, "artefact": {"body": "v1"}, "to": ["human:ana"]},
      actor="agent:drafter")
    d("decide.reject", {"task_id": t, "comment": "Tone is wrong.",
                        "request_revision": True}, actor="human:ana")
    d("task.complete", {"task_id": t, "output": {"body": "v2"}}, actor="agent:drafter")
    d("review.request", {"task_id": t, "artefact": {"body": "v2"}, "to": ["human:ana"]},
      actor="agent:drafter")
    d("decide.approve", {"task_id": t, "comment": "Better."}, actor="human:ana")

    # 4. Two reviewers under all_approve: the second approval settles it.
    t = make("contract_clause")
    clause = {"clause": "Indemnity capped at fees paid."}
    d("task.complete", {"task_id": t, "output": clause}, actor="agent:drafter")
    d("review.request", {"task_id": t, "artefact": clause,
                         "to": ["human:ana", "human:bo"], "rule": "all_approve"},
      actor="agent:drafter")
    d("decide.approve", {"task_id": t, "comment": "Acceptable."}, actor="human:ana")
    d("decide.approve", {"task_id": t, "comment": "Agreed."}, actor="human:bo")

    # 5. Abstention on a conflict of interest.
    t = make()
    d("task.complete", {"task_id": t, "output": {"body": "x"}}, actor="agent:drafter")
    d("review.request", {"task_id": t, "artefact": {"body": "x"}, "to": ["human:cy"]},
      actor="agent:drafter")
    d("abstain.declare", {"task_id": t, "reason": "I drafted the underlying policy.",
                          "category": "conflict_of_interest"}, actor="human:cy")

    # 6. Escalation.
    t = make()
    d("escalate.raise", {"original_task_id": t,
                         "new_task": {"kind": "draft_response", "assignee": "human:cy",
                                      "input": {"ticket": "T-1"}}})

    # 7. Cancelled.
    t = make()
    d("control.cancel", {"task_id": t, "reason": "Customer withdrew."})

    # 8. Created and left alone: still a row in tasks.
    make("orphan")

    # 9. A whisper answered, and one left to lapse.
    t = make()
    w = d("whisper.ask", {"task_id": t, "to": ["human:ana"],
                          "question": "Refund above policy limit. Approve?",
                          "deadline_ms": 600_000, "default_if_lapsed": "no",
                          "options": [{"id": "yes", "label": "Approve"},
                                      {"id": "no", "label": "Decline"}]},
          actor="agent:drafter")["whisper_id"]
    d("whisper.answer", {"whisper_id": w, "answer_option": "yes",
                         "comment": "One-off goodwill."}, actor="human:ana")
    d("whisper.ask", {"task_id": t, "to": ["human:bo"], "question": "Second opinion?",
                      "deadline_ms": 60_000, "default_if_lapsed": "no"},
      actor="agent:drafter")

    # 10. A deliberation that closes.
    delib = d("deliberate.open", {"to": HUMANS, "rule": "all_approve",
                                  "question": "Ship the hotfix today?"})["deliberation_id"]
    d("deliberate.comment", {"deliberation_id": delib, "comment": "Low blast radius."})
    for uri, vote in zip(HUMANS, ["yea", "yea", "abstain"]):
        d("deliberate.vote", {"deliberation_id": delib, "vote": vote}, actor=uri)
    d("deliberate.close", {"deliberation_id": delib})

    return d


@pytest.fixture(scope="session")
def chain(driver):
    from chap_analytics import from_coordinator
    return from_coordinator(driver.coord, workspace=driver.workspace)


@pytest.fixture(scope="session")
def envelopes_only(driver):
    """
    The same workspace as an MCP client sees it: audit.read, no server state.

    Held separately so every test can be run against both and the difference
    between them is a property the tests assert rather than a surprise.
    """
    from chap_analytics.load import Chain
    res = driver.coord.dispatch({
        "jsonrpc": "2.0", "id": "r", "method": "audit.read",
        "params": {"workspace": driver.workspace, "from": "human:ana"},
    })
    return Chain(
        workspace=driver.workspace,
        events=res["result"]["entries"],
        state=None,
        source="audit.read",
    )


@pytest.fixture(scope="session")
def f(chain):
    from chap_analytics import frames
    return frames(chain)
