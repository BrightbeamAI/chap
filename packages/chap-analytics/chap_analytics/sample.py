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

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from .load import Chain, from_coordinator

__all__ = ["support_desk", "support_desk_coordinator", "synthetic", "WORKSPACE",
           "SYNTHETIC_WORKSPACE"]

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


def _sync_clock(coord: Any, now: datetime) -> None:
    """
    Keep the coordinator's own clock on the simulated timeline, so the entries
    it mints itself (a lapse notice, for one) carry the same dates as the
    envelopes around them. A coordinator without a settable clock keeps its own.
    """
    if hasattr(coord, "_clock_ms") and coord._clock_ms is not None:
        coord._clock_ms = int(now.timestamp() * 1000) - 1000


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
        _sync_clock(self.coord, self.now)
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


# ============================================================================
#   A workspace with known truth, for checking statistics
# ============================================================================

SYNTHETIC_WORKSPACE = "wsp_synthetic"


def synthetic(seed: int = 0, *, tasks: int = 300, days: int = 20,
              reviewers: tuple[str, ...] = ("human:ana", "human:ben", "human:cal"),
              reviewer_weights: tuple[float, ...] | None = None,
              agents: tuple[str, ...] = ("agent:drafter",),
              outcome_model: str = "fixed",
              override_rate: float = 0.30, reject_rate: float = 0.05,
              refine_share: float = 0.60,
              quorum_share: float = 0.0, agreement: float = 0.9,
              latency_minutes: tuple[float, float] = (5.0, 240.0),
              open_share: float = 0.05,
              whisper_rate: float = 0.0, lapse_rate: float = 0.3,
              handoffs: int = 0, handoff_accept: float = 0.8,
              delegator_reviews: bool = False,
              mode: str = "trial",
              drift: tuple[int, float] | None = None,
              strictness: tuple[float, ...] | None = None,
              agent_quality: tuple[float, ...] | None = None,
              envelopes_only: bool = False) -> Chain:
    """
    A workspace generated from stated rates, so a statistic can be checked
    against the truth that produced it.

    ``outcome_model`` picks how decisions are drawn. ``fixed`` draws each
    decision from ``override_rate`` and ``reject_rate`` whatever the agent's
    confidence, which is the setting for checking rates. ``calibrated``,
    ``overconfident`` and ``underconfident`` draw approval with a probability
    that follows the confidence the agent reported, exactly, too high, or too
    low, which is the setting for checking calibration.

    ``quorum_share`` sends that share of tasks to two reviewers under
    ``quorum:2``; the second reviewer repeats the first's decision with
    probability ``agreement``. ``open_share`` leaves that share of the last
    tasks undecided, so latency statistics have censored rows to handle.
    ``reviewer_weights`` skews who gets asked, for concentration checks, and
    ``delegator_reviews`` has the person who delegated a task review it as
    well, for separation-of-duties checks. ``drift=(after, rate)`` switches
    the override rate to ``rate`` once ``after`` tasks have been decided, for
    drift-detection checks. ``strictness`` adds to the override rate per
    reviewer and ``agent_quality`` subtracts from it per agent, so a model
    that separates the two can be checked against what generated them; both
    apply under every outcome model, while ``drift`` and ``reject_rate``
    apply under ``fixed``.

    Generating it needs ``chap-coordinator``.
    """
    gen = _Synthetic(seed, tasks=tasks, days=days, reviewers=reviewers,
                     reviewer_weights=reviewer_weights, agents=agents,
                     outcome_model=outcome_model, override_rate=override_rate,
                     reject_rate=reject_rate, refine_share=refine_share,
                     quorum_share=quorum_share, agreement=agreement,
                     latency_minutes=latency_minutes, open_share=open_share,
                     whisper_rate=whisper_rate, lapse_rate=lapse_rate,
                     handoffs=handoffs, handoff_accept=handoff_accept,
                     delegator_reviews=delegator_reviews, mode=mode, drift=drift,
                     strictness=strictness, agent_quality=agent_quality)
    gen.run()
    if envelopes_only:
        entries = gen.call("audit.read", {}, gen.delegator)["entries"]
        return Chain(workspace=SYNTHETIC_WORKSPACE, events=entries, state=None,
                     source="audit.read")
    return from_coordinator(gen.coord, workspace=SYNTHETIC_WORKSPACE)


class _Synthetic:
    # modes/1.0 is left off: under it a trial task opens its own review on
    # task.complete, and the generator wants to choose the reviewers and rule.
    PROFILES = ["core/1.0", "review/1.0", "whisper/1.0", "handoff/1.0"]
    KINDS = ["draft_reply", "summary", "classification"]
    PATHS = ["/reply", "/reply", "/summary", "/label", "/amount"]

    def __init__(self, seed: int, **k: Any):
        try:
            from chap_coordinator import Coordinator, CoordinatorOptions
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError(
                "Generating a synthetic chain needs chap-coordinator: "
                "pip install 'chap-analytics[coordinator]'"
            ) from exc
        self.k = k
        self.rnd = random.Random(seed)
        self.coord = Coordinator(CoordinatorOptions(default_profiles=self.PROFILES,
                                                    deterministic_clock=True,
                                                    enable_chain=True))
        self.now = datetime(2026, 4, 6, 9, 0, tzinfo=timezone.utc)
        self.delegator = "service:intake"
        self.reviewers = list(k["reviewers"])
        self.weights = list(k["reviewer_weights"] or [1.0] * len(self.reviewers))
        self.agents = list(k["agents"])
        self.decided = 0

    def call(self, method: str, params: dict | None = None, actor: str | None = None) -> dict:
        _sync_clock(self.coord, self.now)
        res = self.coord.dispatch({
            "jsonrpc": "2.0", "id": method, "method": method,
            "params": {"workspace": SYNTHETIC_WORKSPACE, "from": actor or self.delegator,
                       "ts": self.now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                       **(params or {})}})
        if "error" in res:
            raise RuntimeError(f"{method} refused: {res['error']['code']} "
                               f"{res['error']['message']}")
        return res.get("result", {})

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)

    def run(self) -> None:
        k = self.k
        self.call("workspace.create", {"profiles": self.PROFILES, "mode": k["mode"]})
        self.call("participant.join", {"type": "service", "role": "intake"})
        for a in self.agents:
            self.call("participant.join", {"type": "agent", "role": "drafter"}, a)
        for r in self.reviewers:
            self.call("participant.join", {"type": "human", "role": "reviewer"}, r)

        days = max(1, k["days"])
        per_day = [k["tasks"] // days + (1 if i < k["tasks"] % days else 0) for i in range(days)]
        pending: list[tuple[str, list[str], float]] = []
        n_open = int(round(k["tasks"] * k["open_share"]))
        for day in range(days):
            self.now = self.now.replace(hour=9, minute=0) + timedelta(days=1 if day else 0)
            for _ in range(per_day[day]):
                pending.append(self.task_arrives())
                # Decide what is pending, apart from the tail left open.
                while len(pending) > (n_open if day == days - 1 else 0) and self.rnd.random() < 0.85:
                    self.decide(*pending.pop(0))
            if k["handoffs"] and day == 1:
                self.some_handoffs()

    def pick_reviewers(self) -> tuple[list[str], str]:
        if self.rnd.random() < self.k["quorum_share"] and len(self.reviewers) >= 2:
            first = self.rnd.choices(self.reviewers, weights=self.weights)[0]
            others = [r for r in self.reviewers if r != first]
            return [first, self.rnd.choice(others)], "quorum:2"
        chosen = self.rnd.choices(self.reviewers, weights=self.weights)[0]
        to = [chosen]
        if self.k["delegator_reviews"]:
            to = [self.delegator]
        return to, "any_one_approves"

    def task_arrives(self) -> tuple[str, list[str], float]:
        self.advance(self.rnd.uniform(5, 40))
        agent = self.rnd.choice(self.agents)
        kind = self.rnd.choice(self.KINDS)
        confidence = round(self.rnd.uniform(0.5, 0.98), 2)
        tid = self.call("task.create", {
            "kind": kind, "assignee": agent, "mode": self.k["mode"],
            "routing_hints": {"confidence": f"{confidence:.2f}"},
            "input": {"item": self.rnd.randint(1000, 9999)},
        })["task_id"]
        self.advance(self.rnd.uniform(0.5, 3))
        output = {"reply": "Draft text.", "summary": "Draft summary.",
                  "label": "general", "amount": 0}
        self.call("task.complete", {"task_id": tid, "output": output,
                                    "confidence": f"{confidence:.2f}"}, agent)
        to, rule = self.pick_reviewers()
        self.call("review.request", {"task_id": tid, "artefact": output,
                                     "to": to, "rule": rule}, agent)
        if self.rnd.random() < self.k["whisper_rate"]:
            self.a_whisper(tid, agent, to[0])
        return tid, to, confidence

    def draw_decision(self, confidence: float, reviewer: str | None = None, agent: str | None = None) -> str:
        k = self.k
        model = k["outcome_model"]
        if model == "fixed":
            rate = k["override_rate"]
            if k["drift"] and self.decided >= k["drift"][0]:
                rate = k["drift"][1]
            if k.get("strictness") and reviewer in self.reviewers:
                rate += k["strictness"][self.reviewers.index(reviewer)]
            if k.get("agent_quality") and agent in self.agents:
                rate -= k["agent_quality"][self.agents.index(agent)]
            rate = min(max(rate, 0.0), 1.0 - k["reject_rate"])
            r = self.rnd.random()
            if r < rate:
                return "override"
            if r < rate + k["reject_rate"]:
                return "reject"
            return "approve"
        shift = {"calibrated": 0.0, "overconfident": -0.25, "underconfident": 0.15}[model]
        p_approve = confidence + shift
        if k.get("strictness") and reviewer in self.reviewers:
            p_approve -= k["strictness"][self.reviewers.index(reviewer)]
        if k.get("agent_quality") and agent in self.agents:
            p_approve += k["agent_quality"][self.agents.index(agent)]
        p_approve = min(0.99, max(0.01, p_approve))
        return "approve" if self.rnd.random() < p_approve else "override"

    def decide(self, tid: str, reviewers: list[str], confidence: float) -> None:
        lo, hi = self.k["latency_minutes"]
        # Log-uniform latency: most decisions come quickly, a few take long.
        self.advance(math.exp(self.rnd.uniform(math.log(lo), math.log(hi))))
        agent = self.coord.get_workspace(SYNTHETIC_WORKSPACE).tasks[tid].assignee
        first = self.draw_decision(confidence, reviewers[0], agent)
        self.decided += 1
        decisions = [first]
        for _ in reviewers[1:]:
            same = self.rnd.random() < self.k["agreement"]
            decisions.append(first if same else ("approve" if first != "approve" else "override"))
        for who, kind in zip(reviewers, decisions):
            state = self.coord.get_workspace(SYNTHETIC_WORKSPACE).tasks[tid].state
            if state != "review_requested":
                break
            if kind == "override":
                path = self.rnd.choice(self.PATHS)
                refining = self.rnd.random() < self.k["refine_share"]
                self.call("decide.override", {
                    "task_id": tid, "rationale": "Adjusted.", "intent_preserved": refining,
                    "tags": ["tone"] if refining else ["substance"],
                    "diff": [{"op": "replace", "path": path,
                              "value": 1 if path == "/amount" else "Edited."}]}, who)
            elif kind == "reject":
                self.call("decide.reject", {"task_id": tid, "comment": "No."}, who)
            else:
                self.call("decide.approve", {"task_id": tid}, who)
            self.advance(self.rnd.uniform(1, 30))

    def a_whisper(self, tid: str, agent: str, human: str) -> None:
        wid = self.call("whisper.ask", {
            "task_id": tid, "to": [human], "deadline_ms": 20 * 60_000,
            "question": "Proceed?", "default_if_lapsed": "no",
            "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]},
            agent)["whisper_id"]
        if self.rnd.random() < self.k["lapse_rate"]:
            ws = self.coord.get_workspace(SYNTHETIC_WORKSPACE)
            asked = datetime.fromisoformat(ws.whispers[wid].asked_at.replace("Z", "+00:00"))
            cutoff = (asked + timedelta(minutes=21)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            self.coord.check_whisper_lapses(SYNTHETIC_WORKSPACE, now=cutoff)
            self.advance(25)
        else:
            self.advance(self.rnd.uniform(1, 15))
            self.call("whisper.answer", {"whisper_id": wid, "answer_option": "yes"}, human)

    def some_handoffs(self) -> None:
        for _ in range(self.k["handoffs"]):
            a, b = self.rnd.sample(self.reviewers, 2)
            self.advance(self.rnd.uniform(3, 10))
            tid = self.call("task.create", {"kind": "manual", "assignee": a,
                                            "input": {"item": 1}})["task_id"]
            hid = self.call("handoff.propose", {"to": b, "tasks": [{"task_id": tid}],
                                                "summary": "Take this one."}, a)["handoff_id"]
            self.advance(self.rnd.uniform(2, 40))
            if self.rnd.random() < self.k["handoff_accept"]:
                self.call("handoff.accept", {"handoff_id": hid}, b)
            else:
                self.call("handoff.decline", {"handoff_id": hid, "reason": "Full."}, b)
