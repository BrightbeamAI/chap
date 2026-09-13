"""
A realistic chain to try the package on.

``support_desk()`` drives a week at a support desk against a real coordinator
and returns the result as a :class:`~chap_analytics.load.Chain`. An agent
drafts replies to customer tickets and three people review them. Over the
week they approve most, correct some and send a few back. One declares a
conflict of interest, work changes hands at a shift change, the agent asks two
questions and gets one answer, a policy exception goes to a vote, and one
ticket is escalated to legal. Every one of those actions is a CHAP envelope.

    from chap_analytics import frames
    from chap_analytics.sample import support_desk

    f = frames(support_desk())
    f.overrides.groupby("top_path").size()

The chain is generated on each call, on a simulated clock stamped into every
envelope, so it reflects the coordinator that is installed and is the same
for the same seed. Generating it needs ``chap-coordinator``, which is the
``coordinator`` extra; reading the result needs pandas alone.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .load import Chain, from_coordinator

__all__ = ["support_desk", "support_desk_coordinator", "WORKSPACE"]

WORKSPACE = "wsp_support"
PROFILES = ["core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0",
            "handoff/1.0", "control/1.0"]
HUMANS = ["human:maya", "human:sam", "human:priya"]
AGENT = "agent:drafter"
INTAKE = "service:intake"
CATEGORIES = ["refund", "shipping", "billing", "account"]

#: Why reviewers correct a draft in this sample. Tone is the most common reason
#: offered, and the correction with the cheapest fix: one line in the prompt.
CORRECTIONS = [
    ("tone-softened", "/reply", True, "Too curt for a customer who has waited a week.", None),
    ("tone-softened", "/reply", True, "Opens with the policy; the apology comes first.", None),
    ("tone-softened", "/reply", True, "Reads as a form letter. Name the order.", None),
    ("policy-cite-missing", "/reply", True, "Cite the returns window; customers ask.", "POL-RET-14"),
    ("factual-fix", "/reply", False, "Wrong carrier named.", None),
    ("refund-amount", "/refund_amount", False, "Shipping is refundable on a damaged item.", "POL-REFUND-3"),
]


def support_desk(seed: int = 7, *, envelopes_only: bool = False) -> Chain:
    """
    A week at the support desk, as a chain.

    With ``envelopes_only`` the chain is what an MCP client would hold after
    ``audit.read``: the envelope stream alone. Otherwise it carries the
    coordinator's snapshot as well, as a SQLite file would.
    """
    desk = _Desk(seed)
    desk.week()
    if envelopes_only:
        entries = desk.call("audit.read", {}, "human:maya")["entries"]
        return Chain(workspace=WORKSPACE, events=entries, state=None, source="audit.read")
    return from_coordinator(desk.coord, workspace=WORKSPACE)


def support_desk_coordinator(seed: int = 7) -> Any:
    """The coordinator after the week, for anyone who wants to keep driving it."""
    desk = _Desk(seed)
    desk.week()
    return desk.coord


class _Desk:
    """Drives the coordinator with a simulated clock stamped into every envelope."""

    def __init__(self, seed: int):
        try:
            from chap_coordinator import Coordinator, CoordinatorOptions
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError(
                "Generating the sample chain needs chap-coordinator: "
                "pip install 'chap-analytics[coordinator]'"
            ) from exc
        self.rnd = random.Random(seed)
        self.coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES,
                                                    deterministic_clock=True,
                                                    enable_chain=True))
        self.now = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)  # a Monday
        self.ticket = 1040

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)

    def call(self, method: str, params: dict | None = None, actor: str = INTAKE) -> dict:
        res = self.coord.dispatch({
            "jsonrpc": "2.0", "id": method, "method": method,
            "params": {"workspace": WORKSPACE, "from": actor,
                       "ts": self.now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                       **(params or {})}})
        if "error" in res:
            raise RuntimeError(
                f"{method} refused: {res['error']['code']} {res['error']['message']}. "
                "The sample drives the coordinator that is installed, so a refusal "
                "means the protocol has moved and the sample has to follow.")
        return res.get("result", {})

    @property
    def ws(self):
        return self.coord.get_workspace(WORKSPACE)

    # -- the week -----------------------------------------------------------

    def week(self) -> None:
        self.call("workspace.create", {"profiles": PROFILES})
        self.call("participant.join", {"type": "service", "role": "intake"}, INTAKE)
        self.call("participant.join", {"type": "agent", "role": "drafter"}, AGENT)
        for uri in HUMANS:
            self.call("participant.join", {"type": "human", "role": "support"}, uri)

        waiting: list[tuple[str, list[str], float]] = []
        for day in range(5):
            self.now = self.now.replace(hour=9, minute=0) + timedelta(days=1 if day else 0)
            for _ in range(self.rnd.randint(5, 7)):
                self.advance(self.rnd.uniform(10, 50))
                tid, confidence = self.ticket_arrives()
                waiting.append((tid, list(self.ws.tasks[tid].review.requested_to), confidence))
                # Most reviews happen the same day. A few wait.
                if self.rnd.random() < 0.8:
                    self.review(*waiting.pop())
            if day == 0:
                self.a_question_answered()
            if day == 1:
                self.a_conflict_of_interest()
            if day == 2:
                self.a_shift_change()
                self.a_question_that_lapsed()
            if day == 3:
                self.an_escalation()
                self.a_policy_exception_put_to_a_vote()
            if day == 4:
                # Friday afternoon. Some work is still open when the week ends,
                # and the tables have to say so.
                while len(waiting) > 3:
                    self.review(*waiting.pop(0))

    def ticket_arrives(self) -> tuple[str, float]:
        """A ticket comes in, the agent drafts a reply and asks for review."""
        self.ticket += 1
        category = self.rnd.choice(CATEGORIES)
        amount = self.rnd.choice([0, 0, 18, 42, 65, 140, 260]) if category == "refund" else 0
        # The agent is less sure about large refunds, which is what a
        # calibration analysis should be able to see.
        confidence = round(self.rnd.uniform(0.55, 0.8) if amount > 100
                           else self.rnd.uniform(0.7, 0.97), 2)
        tid = self.call("task.create", {
            "kind": "draft_reply", "assignee": AGENT,
            "routing_hints": {"confidence": f"{confidence:.2f}",
                              "criticality": "high" if amount > 100 else "low"},
            "input": {"ticket": f"T-{self.ticket}", "category": category,
                      "refund_requested": amount},
        })["task_id"]
        self.advance(self.rnd.uniform(1, 4))
        output = {"reply": f"Hello, about your {category} request on T-{self.ticket}: ...",
                  "refund_amount": amount}
        self.call("task.complete", {"task_id": tid, "output": output,
                                    "confidence": f"{confidence:.2f}"}, AGENT)
        # A large refund needs two people to agree.
        self.call("review.request", {
            "task_id": tid, "artefact": output,
            "to": self.rnd.sample(HUMANS, 2) if amount > 100 else [self.rnd.choice(HUMANS)],
            "rule": "quorum:2" if amount > 100 else "any_one_approves",
        }, AGENT)
        return tid, confidence

    def review(self, tid: str, reviewers: list[str], confidence: float) -> None:
        """Reviewers decide. The agent's weaker drafts are corrected more often."""
        self.advance(self.rnd.uniform(4, 150))
        p_override = 0.6 if confidence < 0.75 else 0.28
        roll = self.rnd.random()
        if roll < p_override:
            tag, path, refining, rationale, policy = self.rnd.choice(CORRECTIONS)
            value = 60 if path == "/refund_amount" else "Hello, I am sorry for the wait. ..."
            params = {"task_id": tid, "rationale": rationale, "tags": [tag],
                      "intent_preserved": refining,
                      "diff": [{"op": "replace", "path": path, "value": value}]}
            if policy:
                params["policy_refs"] = [policy]
            self.call("decide.override", params, reviewers[0])
        elif roll < p_override + 0.08:
            self.call("decide.reject", {"task_id": tid, "request_revision": True,
                                        "comment": "Check the order history first."},
                      reviewers[0])
            self.advance(self.rnd.uniform(10, 40))
            revised = {"reply": "Hello, having checked ...", "refund_amount": 0}
            self.call("task.complete", {"task_id": tid, "output": revised}, AGENT)
            self.call("review.request", {"task_id": tid, "to": reviewers, "artefact": revised}, AGENT)
            self.advance(self.rnd.uniform(5, 60))
            self.call("decide.approve", {"task_id": tid}, reviewers[0])
        else:
            for who in reviewers:
                self.call("decide.approve", {"task_id": tid}, who)
                self.advance(self.rnd.uniform(2, 45))

    def a_question_answered(self) -> None:
        tid, _ = self.ticket_arrives()
        wid = self.call("whisper.ask", {
            "task_id": tid, "to": ["human:maya"], "deadline_ms": 30 * 60_000,
            "question": "Is a partial refund acceptable here?",
            "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}],
            "default_if_lapsed": "no"}, AGENT)["whisper_id"]
        self.advance(7)
        self.call("whisper.answer", {"whisper_id": wid, "answer_option": "yes"}, "human:maya")
        self.advance(3)
        self.call("decide.approve", {"task_id": tid}, self.ws.tasks[tid].review.requested_to[0])

    def a_question_that_lapsed(self) -> None:
        tid, _ = self.ticket_arrives()
        wid = self.call("whisper.ask", {
            "task_id": tid, "to": ["human:sam"], "deadline_ms": 30 * 60_000,
            "question": "Waive the restocking fee?", "default_if_lapsed": "no",
            "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]},
            AGENT)["whisper_id"]
        # The question goes unanswered. The coordinator's lapse check runs on
        # its own clock.
        asked = datetime.fromisoformat(self.ws.whispers[wid].asked_at.replace("Z", "+00:00"))
        cutoff = (asked + timedelta(minutes=31)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        self.coord.check_whisper_lapses(WORKSPACE, now=cutoff)
        self.advance(45)
        self.call("decide.approve", {"task_id": tid}, self.ws.tasks[tid].review.requested_to[0])

    def a_conflict_of_interest(self) -> None:
        tid, _ = self.ticket_arrives()
        self.advance(12)
        self.call("abstain.declare", {"task_id": tid, "category": "conflict_of_interest",
                                      "reason": "I know this customer personally."},
                  self.ws.tasks[tid].review.requested_to[0])

    def a_shift_change(self) -> None:
        # Three tickets need a human-written reply and land on Maya late in
        # the day. She hands them to Sam at the end of her shift. A handoff
        # moves the work, so the work has to be hers to move.
        self.now = self.now.replace(hour=17, minute=10)
        mine = []
        for _ in range(3):
            self.advance(self.rnd.uniform(2, 6))
            self.ticket += 1
            mine.append(self.call("task.create", {
                "kind": "manual_reply", "assignee": "human:maya",
                "input": {"ticket": f"T-{self.ticket}", "category": "complaint"}})["task_id"])
        self.advance(8)
        hid = self.call("handoff.propose", {
            "to": "human:sam", "tasks": [{"task_id": t} for t in mine],
            "summary": "Three complaints still to answer; I am off at half past."},
            "human:maya")["handoff_id"]
        self.advance(9)
        self.call("handoff.accept", {"handoff_id": hid}, "human:sam")
        for tid in mine:
            self.advance(self.rnd.uniform(8, 25))
            self.call("task.complete", {"task_id": tid,
                                        "output": {"reply": "Dear customer, ..."}}, "human:sam")

    def an_escalation(self) -> None:
        tid, _ = self.ticket_arrives()
        self.advance(20)
        self.call("escalate.raise", {
            "original_task_id": tid, "reason": "Customer threatens legal action.",
            "new_task": {"kind": "legal_review", "assignee": "human:priya",
                         "input": {"ticket": f"T-{self.ticket}"}}}, "human:sam")

    def a_policy_exception_put_to_a_vote(self) -> None:
        did = self.call("deliberate.open", {
            "to": HUMANS, "rule": "any_one_approves",
            "question": "Extend the returns window for T-1061 to 30 days?"},
            "human:priya")["deliberation_id"]
        for who, vote in zip(HUMANS, ["yea", "yea", "nay"]):
            self.advance(self.rnd.uniform(3, 20))
            self.call("deliberate.vote", {"deliberation_id": did, "vote": vote}, who)
        self.call("deliberate.close", {"deliberation_id": did}, "human:priya")
