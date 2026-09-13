"""
A week at a support desk, as the analytics layer sees it.

An agent drafts replies to customer tickets; three people review them. Over
the week they approve most, correct some, send a few back, hand work over at a
shift change, ask and answer questions, put one policy exception to a vote and
escalate one ticket to legal. Every one of those actions is a CHAP envelope,
and this script drives them against a real coordinator, reads the chain back
both ways, and prints the analyses that the tables make routine.

Nothing here is fitted or inferred. Each section is a count or a median, each
names the decision it informs, and the sample is small enough that the script
says so. The point is not the numbers; it is that a week of ordinary review
work produced them without anyone doing anything extra.

    pip install -e packages/coordinator-py -e packages/chap-analytics
    python packages/chap-analytics/examples/support_desk.py

The workspace is generated, not recorded, so the output reflects the
coordinator on this commit. It is deterministic for a given seed.
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
from chap_coordinator import Coordinator, CoordinatorOptions

from chap_analytics import Frames, frames, from_coordinator, redact_artefacts
from chap_analytics.load import Chain

PROFILES = ["core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0",
            "handoff/1.0", "control/1.0"]

HUMANS = ["human:maya", "human:sam", "human:priya"]
AGENT = "agent:drafter"
INTAKE = "service:intake"
CATEGORIES = ["refund", "shipping", "billing", "account"]

#: Why reviewers correct a draft, and how often. Tone dominates, which is the
#: usual finding and the one with the cheapest fix.
CORRECTIONS = [
    ("tone-softened", "/reply", True, "Too curt for a customer who has waited a week.", None),
    ("tone-softened", "/reply", True, "Opens with the policy instead of the apology.", None),
    ("policy-cite-missing", "/reply", True, "Cite the returns window; customers ask.", "POL-RET-14"),
    ("factual-fix", "/reply", False, "Wrong carrier named.", None),
    ("refund-amount", "/refund_amount", False, "Shipping is refundable on a damaged item.", "POL-REFUND-3"),
]


class Desk:
    """Drives the coordinator with a simulated clock stamped into every envelope."""

    def __init__(self, seed: int):
        self.rnd = random.Random(seed)
        self.coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES,
                                                    deterministic_clock=True))
        self.now = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)  # Monday
        self.ticket = 1040

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)

    def call(self, method: str, params: dict | None = None, actor: str = INTAKE) -> dict:
        envelope = {"jsonrpc": "2.0", "id": method, "method": method,
                    "params": {"workspace": "wsp_support", "from": actor,
                               "ts": self.now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                               **(params or {})}}
        res = self.coord.dispatch(envelope)
        if "error" in res:
            raise SystemExit(f"{method} refused: {res['error']['code']} {res['error']['message']}")
        return res.get("result", {})

    @property
    def ws(self):
        return self.coord.get_workspace("wsp_support")

    # -- the scenario -------------------------------------------------------

    def open(self) -> None:
        self.call("workspace.create", {"profiles": PROFILES})
        self.call("participant.join", {"type": "service", "role": "intake"}, INTAKE)
        self.call("participant.join", {"type": "agent", "role": "drafter"}, AGENT)
        for uri in HUMANS:
            self.call("participant.join", {"type": "human", "role": "support"}, uri)

    def ticket_arrives(self) -> tuple[str, str, float]:
        """A ticket comes in and the agent drafts a reply for it."""
        self.ticket += 1
        category = self.rnd.choice(CATEGORIES)
        amount = self.rnd.choice([0, 0, 18, 42, 65, 140, 260]) if category == "refund" else 0
        # The agent is less sure about refunds and about anything over the
        # threshold, which is what a calibration analysis should be able to see.
        confidence = round(self.rnd.uniform(0.55, 0.8) if amount > 100
                           else self.rnd.uniform(0.7, 0.97), 2)
        hints = {"confidence": f"{confidence:.2f}",
                 "criticality": "high" if amount > 100 else "low"}
        tid = self.call("task.create", {
            "kind": "draft_reply", "assignee": AGENT, "routing_hints": hints,
            "input": {"ticket": f"T-{self.ticket}", "category": category,
                      "refund_requested": amount},
        })["task_id"]
        self.advance(self.rnd.uniform(1, 4))
        output = {"reply": f"Hello, about your {category} request on T-{self.ticket}: ...",
                  "refund_amount": amount}
        self.call("task.complete", {"task_id": tid, "output": output,
                                    "confidence": f"{confidence:.2f}"}, AGENT)
        to = self.rnd.sample(HUMANS, 2) if amount > 100 else [self.rnd.choice(HUMANS)]
        self.call("review.request", {
            "task_id": tid, "artefact": output, "to": to,
            "rule": "quorum:2" if amount > 100 else "any_one_approves",
        }, AGENT)
        return tid, category, confidence

    def review(self, tid: str, reviewers: list[str], confidence: float) -> None:
        """Reviewers decide, with the agent's weaker drafts corrected more often."""
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
            self.call("task.complete", {"task_id": tid,
                                        "output": {"reply": "Hello, having checked ...",
                                                   "refund_amount": 0}}, AGENT)
            self.call("review.request", {"task_id": tid, "to": reviewers,
                                         "artefact": {"reply": "Hello, having checked ...",
                                                      "refund_amount": 0}}, AGENT)
            self.advance(self.rnd.uniform(5, 60))
            self.call("decide.approve", {"task_id": tid}, reviewers[0])
        else:
            for who in reviewers:
                self.call("decide.approve", {"task_id": tid}, who)
                self.advance(self.rnd.uniform(2, 45))

    def week(self) -> None:
        self.open()
        open_tasks: list[tuple[str, list[str], float]] = []
        for day in range(5):
            self.now = self.now.replace(hour=9, minute=0) + timedelta(days=1 if day else 0)
            for _ in range(self.rnd.randint(5, 7)):
                self.advance(self.rnd.uniform(10, 50))
                tid, _category, confidence = self.ticket_arrives()
                reviewers = list(self.ws.tasks[tid].review.requested_to)
                open_tasks.append((tid, reviewers, confidence))
                # Most reviews happen the same day; a few wait.
                if self.rnd.random() < 0.8:
                    self.review(*open_tasks.pop())
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
                # Friday afternoon: some work is still open when the week ends,
                # and the tables have to say so rather than count it as fast.
                while len(open_tasks) > 3:
                    self.review(*open_tasks.pop(0))

    def a_question_answered(self) -> None:
        tid, _, _ = self.ticket_arrives()
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
        tid, _, _ = self.ticket_arrives()
        wid = self.call("whisper.ask", {
            "task_id": tid, "to": ["human:sam"], "deadline_ms": 30 * 60_000,
            "question": "Waive the restocking fee?", "default_if_lapsed": "no",
            "options": [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]}, AGENT)["whisper_id"]
        # Nobody answers. The coordinator's lapse check runs on its own clock.
        asked = self.ws.whispers[wid].asked_at
        cutoff = datetime.fromisoformat(asked.replace("Z", "+00:00")) + timedelta(minutes=31)
        self.coord.check_whisper_lapses("wsp_support", now=cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        self.advance(45)
        self.call("decide.approve", {"task_id": tid}, self.ws.tasks[tid].review.requested_to[0])

    def a_conflict_of_interest(self) -> None:
        tid, _, _ = self.ticket_arrives()
        self.advance(12)
        self.call("abstain.declare", {"task_id": tid, "category": "conflict_of_interest",
                                      "reason": "I know this customer personally."},
                  self.ws.tasks[tid].review.requested_to[0])

    def a_shift_change(self) -> None:
        # Three tickets need a human-written reply and land on Maya late in
        # the day. She hands them to Sam at the end of her shift rather than
        # leave them overnight; a handoff moves the work, so it is hers to move.
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
        tid, _, _ = self.ticket_arrives()
        self.advance(20)
        self.call("escalate.raise", {
            "original_task_id": tid, "reason": "Customer threatens legal action.",
            "new_task": {"kind": "legal_review", "assignee": "human:priya",
                         "input": {"ticket": f"T-{self.ticket}"}}}, "human:sam")

    def a_policy_exception_put_to_a_vote(self) -> None:
        did = self.call("deliberate.open", {
            "to": HUMANS, "rule": "any_one_approves",
            "question": "Extend the returns window for T-1061 to 30 days?"}, "human:priya")["deliberation_id"]
        for who, vote in zip(HUMANS, ["yea", "yea", "nay"]):
            self.advance(self.rnd.uniform(3, 20))
            self.call("deliberate.vote", {"deliberation_id": did, "vote": vote}, who)
        self.call("deliberate.close", {"deliberation_id": did}, "human:priya")

    def audit(self) -> list[dict]:
        return self.call("audit.read", {}, "human:maya")["entries"]


# -- the report ---------------------------------------------------------------

def section(title: str, informs: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}\n{informs}\n")


def show(df: pd.DataFrame) -> None:
    print(df.to_string(index=False) if len(df) else "  (nothing)")


def report(f: Frames) -> None:  # noqa: C901 - one block per section
    tasks, decisions, overrides = f.tasks, f.decisions, f.overrides
    drafts = tasks[tasks["kind"] == "draft_reply"]

    print("A week at the support desk")
    print("==========================")
    print(f.summary())
    kinds = tasks["kind"].value_counts()
    print(f"\n{len(tasks)} tasks: " + ", ".join(f"{n} {k}" for k, n in kinds.items())
          + f". {int(tasks['settled'].sum())} settled, {int((~tasks['settled']).sum())} still open; "
          f"{decisions['reviewer'].nunique()} people decided.")
    print("Counts and medians only: a week is too little for anything with a "
          "confidence interval, and the tables say so rather than pretending.")

    section("1. How the week ended",
            "The overridden share is the supervision signal; the rest is throughput.")
    show(drafts["outcome"].value_counts().rename_axis("outcome").reset_index(name="tasks"))

    section("2. What reviewers keep correcting",
            "A field that dominates is a prompt to revise, not a reviewer to retrain.")
    show(f.patch_ops.groupby("top_path").size().rename("corrections").reset_index())

    section("3. Why they corrected it",
            "The top tag names the next prompt change; a policy reference names the policy the prompt should cite.")
    tags = overrides.explode("tags")["tags"].value_counts().rename_axis("tag").reset_index(name="overrides")
    show(tags)
    refs = overrides.explode("policy_refs")["policy_refs"].dropna()
    if len(refs):
        print(f"\nPolicies invoked: {', '.join(sorted(refs.unique()))}")

    section("4. Refining the draft, or reversing it",
            "Refinements are style; reversals are the agent getting the decision wrong.")
    split = overrides["intent_preserved"].map({True: "refined", False: "reversed"}).fillna("unsaid")
    show(split.value_counts().rename_axis("edit").reset_index(name="overrides"))

    section("5. Does the agent's confidence mean anything?",
            "If overridden drafts were not less confident than approved ones, the thresholds are decoration.")
    judged = drafts[drafts["outcome"].isin(["approved", "overridden"]) & drafts["confidence"].notna()]
    by = (judged.groupby("outcome")["confidence"].agg(["count", "median", "min", "max"]).round(2)
          .rename(columns={"count": "n"}).reset_index())
    show(by)
    print("\n  Reliability diagrams and a Brier score are stage 3 of the roadmap; with a "
          "week of data the two medians are all that can be said.")

    section("6. The reviewers",
            "A reviewer who overrides far more than the others is either stricter or getting the harder work; the routing table would say which.")
    league = f.participants[f.participants["kind"] == "human"][
        ["participant", "n_decisions", "n_overrides", "n_abstentions"]].copy()
    lat = decisions.groupby("reviewer")["latency_s"].median().div(60).round(0).rename("median_minutes")
    league = league.merge(lat, left_on="participant", right_index=True, how="left")
    show(league)

    section("7. Time to a decision",
            "Elapsed time, not effort. Open work is censored rather than counted as fast.")
    final = decisions[decisions["is_final"]]["latency_s"].div(60)
    print(f"  {len(final)} reviews settled; median {final.median():.0f} min, "
          f"90th percentile {final.quantile(0.9):.0f} min.")
    print(f"  {int((~tasks['settled']).sum())} tasks still open at the end of the week "
          f"and excluded from every figure above.")
    passes = tasks[tasks["n_reviews"] > 1]
    print(f"  {len(passes)} task(s) were sent back and reviewed again; each pass is measured "
          f"from its own opening.")

    section("8. Questions and handoffs",
            "A lapse means the agent's default stood with no human input; a declined handoff means the proposer misread who should take the work.")
    w = f.whispers
    print(f"  Whispers asked: {len(w)}, answered {int(w['answered'].sum())}, lapsed {int(w['lapsed'].sum())}"
          + (f"; median response {w['response_s'].median() / 60:.0f} min." if w["answered"].any() else "."))
    h = f.handoffs
    for _, row in h.iterrows():
        print(f"  Handoff {row['proposer']} -> {row['recipient']}: {row['resolution']}, "
              f"{row['n_accepted']}/{row['n_tasks']} tasks taken, after {row['response_s'] / 60:.0f} min.")
    d = f.deliberations
    for _, row in d.iterrows():
        print(f"  Deliberation \"{row['question']}\": {row['n_yea']} for, {row['n_nay']} against, "
              f"turnout {row['turnout']:.0%}, outcome {row['outcome'] or 'not carried by the source'}.")
    esc = tasks[tasks["outcome"] == "escalated"]
    print(f"  Escalated: {len(esc)} (to {', '.join(tasks[tasks['supersedes'].notna()]['assignee'].astype(str))}).")


def compare_reads(state: Frames, envelopes: Frames) -> None:
    section("9. The same tables from audit.read alone",
            "An MCP client gets only the envelopes. Everything above is available to it, "
            "and the rows it cannot pin to an id say so.")
    same = (state.tasks["outcome"].value_counts().to_dict()
            == envelopes.tasks["outcome"].value_counts().to_dict())
    print(f"  Outcome counts identical across the two reads: {same}")
    print(f"  Override count: {len(state.overrides)} with state, {len(envelopes.overrides)} from envelopes")
    for name in ("tasks", "whispers", "deliberations", "handoffs"):
        df = envelopes[name]
        print(f"  {name:14} {int(df['id_certain'].sum())}/{len(df)} rows identified beyond doubt from envelopes")
    print("  With state the pairing is settled by what the two records agree on; "
          "from envelopes it is settled where the order of events allows, and flagged where not.")


def show_redaction(coord: Coordinator, intact: Frames) -> None:
    section("10. With the content redacted",
            "Whoever runs the analysis need not see the customer's message. The shape survives; the words go.")
    red = frames(from_coordinator(coord, workspace="wsp_support", redact=redact_artefacts))
    print(f"  Overrides: {len(red.overrides)} redacted, {len(intact.overrides)} intact")
    print(f"  Patch paths kept: {sorted(red.patch_ops['top_path'].unique())}")
    print(f"  Tags kept: {sorted(red.overrides.explode('tags')['tags'].unique())}")
    print(f"  based_on on the first override: {red.overrides.iloc[0]['based_on']!r} "
          f"(was a {type(intact.overrides.iloc[0]['based_on']).__name__})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--export", metavar="PATH",
                        help="also write the audit.read result to a JSON file, "
                             "to load later with from_json()")
    args = parser.parse_args(argv)

    desk = Desk(args.seed)
    desk.week()

    entries = desk.audit()
    with_state = frames(from_coordinator(desk.coord, workspace="wsp_support"))
    from_envelopes = frames(Chain(workspace="wsp_support", events=entries, state=None,
                                  source="audit.read"))

    pd.set_option("display.width", 120)
    report(with_state)
    compare_reads(with_state, from_envelopes)
    show_redaction(desk.coord, with_state)

    if args.export:
        import json
        with open(args.export, "w", encoding="utf-8") as fh:
            json.dump({"workspace": "wsp_support", "entries": entries}, fh, indent=1)
        print(f"\nWrote {len(entries)} audit entries to {args.export}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
