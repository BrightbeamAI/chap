"""
Random workspaces, checked against the coordinator that produced them.

The hand-written tests each encode a case someone thought of. This one
drives a real coordinator with a random sequence of legal calls, reads the
result both ways, and asserts that what the tables say agrees with what the
coordinator holds. Two defects survived a full adversarial review of the code
and were found here instead: a routing choice written back over an assignee a
later handoff had moved, and an acceptance attached to the wrong outstanding
offer with both tasks reported as fact.

Refused calls are skipped, because the coordinator records accepted calls
alone and a chain in the wild is made of those. What is left is a chain the
coordinator accepted in full.

Rows the projection marks ``id_certain`` false are exempt from the per-row
comparisons alone: the counts, the invariants and the stateful read are all
checked unconditionally, and a floor on how many rows are certain is asserted
so the exemption stays small.
"""
from __future__ import annotations

import random

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402
from chap_coordinator import Coordinator, CoordinatorOptions  # noqa: E402

from chap_analytics import frames, from_coordinator  # noqa: E402
from chap_analytics.load import Chain  # noqa: E402

PROFILES = ["core/1.0", "review/1.0", "whisper/1.0", "deliberation/1.0",
            "handoff/1.0", "control/1.0", "routing/1.0"]
HUMANS = ["human:ana", "human:bo", "human:cy"]
AGENTS = ["agent:x", "agent:y"]
RULES = ["any_one_approves", "quorum:2", "all_approve"]
HANDOFF_STATES = {"proposed": "open", "accepted": "accepted", "declined": "declined"}


class Fuzzer:
    def __init__(self, seed: int):
        self.rnd = random.Random(seed)
        self.seed = seed
        self.coord = Coordinator(CoordinatorOptions(default_profiles=PROFILES))
        self.live: list[str] = []
        self.n = 0
        # Some clients stamp their own time into params, as the profile
        # examples do, and the projection has to read it from there. A client
        # does so on every call or on none: a chain that mixes sender clocks
        # with the coordinator's would put two clocks in one duration.
        self.stamps = self.rnd.random() < 0.3
        self.call("workspace.create", {"profiles": PROFILES})
        for uri in HUMANS:
            self.call("participant.join", {"type": "human"}, uri)
        for uri in AGENTS:
            self.call("participant.join", {"type": "agent"}, uri)

    def call(self, method, params=None, actor="human:ana"):
        params = {"workspace": "w", "from": actor, **(params or {})}
        if self.stamps:
            params["ts"] = f"2026-03-01T10:{self.n // 60 % 60:02d}:{self.n % 60:02d}.000Z"
        self.n += 1
        res = self.coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                                   "params": params})
        return None if "error" in res else res.get("result", {})

    @property
    def ws(self):
        return self.coord.get_workspace("w")

    def own_id(self, prefix: str) -> str | None:
        """A caller-supplied id, some of the time, as the profiles allow."""
        return f"{prefix}-{self.seed}-{self.n}" if self.rnd.random() < 0.3 else None

    def step(self) -> None:  # noqa: C901 - a menu of actions
        rnd = self.rnd
        act = rnd.choice([
            "create", "create", "complete", "review", "decide", "decide", "update",
            "escalate", "supersede", "cancel", "pause", "handoff", "whisper",
            "deliberate", "route", "depth", "auto",
        ])
        if act == "create" or not self.live:
            hints = {}
            if rnd.random() < 0.5:
                hints["criticality"] = rnd.choice(["low", "high", "critical"])
            if rnd.random() < 0.5:
                hints["confidence"] = f"0.{rnd.randint(10, 99)}"
            params = {"kind": rnd.choice(["a", "b", "c"]), "input": {"n": self.n},
                      "assignee": rnd.choice(AGENTS)}
            if hints:
                params["routing_hints"] = hints
            if rnd.random() < 0.3:
                params["review_required"] = True
            made = self.call("task.create", params)
            if made:
                self.live.append(made["task_id"])
            return

        t = rnd.choice(self.live)
        if act == "complete":
            self.call("task.complete",
                      {"task_id": t, "output": {"v": rnd.randint(0, 9), "note": "draft"}},
                      self.ws.tasks[t].assignee)
        elif act == "update":
            self.call("task.update",
                      {"task_id": t, "state": rnd.choice(["in_progress", "completed", "declined"])},
                      self.ws.tasks[t].assignee)
        elif act == "review":
            self.call("review.request",
                      {"task_id": t, "artefact": {"v": 1, "note": "draft"},
                       "to": rnd.sample(HUMANS, rnd.randint(1, 3)),
                       "rule": rnd.choice(RULES)}, self.ws.tasks[t].assignee)
        elif act == "decide":
            kind = rnd.choice(["decide.approve", "decide.reject",
                               "decide.override", "abstain.declare"])
            params = {"task_id": t}
            if kind == "decide.reject":
                params["request_revision"] = rnd.random() < 0.6
            if kind == "decide.override":
                params["diff"] = rnd.choice([
                    [{"op": "replace", "path": "/v", "value": 9}],
                    [{"op": "add", "path": "/extra", "value": "x"},
                     {"op": "remove", "path": "/note"}],
                    [{"op": "copy", "from": "/v", "path": "/w"},
                     {"op": "move", "from": "/note", "path": "/memo"}],
                ])
                params["rationale"] = "House style."
            if kind == "abstain.declare":
                params["category"] = "out_of_scope"
            self.call(kind, params, rnd.choice(HUMANS))
        elif act == "escalate":
            spec = {"assignee": rnd.choice(HUMANS), "input": {}}
            if rnd.random() < 0.5:
                spec["kind"] = "esc"
            made = self.call("escalate.raise", {"original_task_id": t, "new_task": spec})
            if made:
                self.live.remove(t)
                self.live.append(made["new_task_id"])
        elif act == "supersede":
            spec = {"kind": "v2", "input": {}}
            if rnd.random() < 0.5:
                spec["assignee"] = rnd.choice(AGENTS + HUMANS)
            if rnd.random() < 0.3:
                spec["review_required"] = True
            made = self.call("control.supersede",
                             {"task_id": t, "successor_task": spec, "reason": "Redo."})
            if made:
                self.live.remove(t)
                self.live.append(made["new_task_id"])
        elif act == "cancel":
            if self.call("control.cancel", {"task_id": t, "reason": "no longer needed"}):
                self.live.remove(t)
        elif act == "pause":
            if self.call("control.pause", {"task_id": t, "reason": "hold"}):
                # Pausing a paused task is a defined transition that changes
                # nothing, and the resume after it has to clear the pause in
                # one call. The projection has to hold the first captured
                # state, as the coordinator does.
                if rnd.random() < 0.3:
                    self.call("control.pause", {"task_id": t, "reason": "still holding"})
                if rnd.random() < 0.7:
                    self.call("control.resume", {"task_id": t})
        elif act == "handoff":
            params = {"to": rnd.choice(HUMANS + ["group:oncall"]), "tasks": [{"task_id": t}]}
            hid = self.own_id("hnd")
            if hid:
                params["handoff_id"] = hid
            made = self.call("handoff.propose", params, self.ws.tasks[t].assignee)
            if made and rnd.random() < 0.7:
                who = rnd.choice(HUMANS)
                if rnd.random() < 0.6:
                    self.call("handoff.accept", {"handoff_id": made["handoff_id"]}, who)
                else:
                    self.call("handoff.decline",
                              {"handoff_id": made["handoff_id"], "reason": "Not mine."}, who)
        elif act == "whisper":
            params = {"task_id": t, "to": [rnd.choice(HUMANS + ["group:oncall"])],
                      "question": f"q{self.n}", "deadline_ms": rnd.choice([0, 600_000]),
                      "default_if_lapsed": "no"}
            wid = self.own_id("wsp")
            if wid:
                params["whisper_id"] = wid
            made = self.call("whisper.ask", params, self.ws.tasks[t].assignee)
            if made and rnd.random() < 0.5:
                who = rnd.choice(HUMANS)
                self.call("whisper.answer",
                          {"whisper_id": made["whisper_id"],
                           rnd.choice(["answer", "answer_text"]): "yes"}, who)
            if rnd.random() < 0.5:
                self.coord.check_whisper_lapses("w")
        elif act == "deliberate":
            params = {"to": rnd.sample(HUMANS, rnd.randint(1, 3)),
                      "rule": "any_one_approves", "question": f"Q{self.n}"}
            did = self.own_id("dlb")
            if did:
                params["deliberation_id"] = did
            made = self.call("deliberate.open", params, rnd.choice(HUMANS))
            if not made:
                return
            for who in rnd.sample(HUMANS, rnd.randint(0, 2)):
                self.call("deliberate.vote",
                          {"deliberation_id": made["deliberation_id"],
                           "vote": rnd.choice(["yea", "nay"])}, who)
            if rnd.random() < 0.5:
                self.call("deliberate.close", {"deliberation_id": made["deliberation_id"]})
        elif act == "route":
            self.call("task.route", {"task_id": t, "candidates": list(AGENTS)})
        elif act == "depth":
            self.call("review.depth", {"task_id": t})
        elif act == "auto":
            self.call("escalate.auto", {"task_id": t, "default_escalation_target": "human:bo"})


def workspace(seed: int) -> Fuzzer:
    f = Fuzzer(seed)
    for _ in range(f.rnd.randint(6, 24)):
        f.step()
    return f


def read_both(f: Fuzzer):
    entries = f.call("audit.read", {})["entries"]
    return {"envelopes": frames(Chain(workspace="w", events=entries, state=None,
                                      source="audit.read")),
            "state": frames(from_coordinator(f.coord, workspace="w"))}


SEEDS = list(range(120))


def sure(df: pd.DataFrame, key: str, ident: str):
    """Rows the read vouches for, indexed by their id."""
    rows = df.set_index(key)
    return rows[rows["id_certain"].fillna(False)]


@pytest.mark.parametrize("seed", SEEDS)
def test_both_reads_match_the_coordinator_that_produced_the_chain(seed):
    f = workspace(seed)
    ws = f.ws
    for label, frame in read_both(f).items():
        tasks = frame.tasks.set_index("task_id")
        assert len(tasks) == len(ws.tasks), (
            f"{label}: {len(tasks)} task rows against {len(ws.tasks)} tasks")
        for tid, task in ws.tasks.items():
            if tid not in tasks.index or not bool(tasks.loc[tid, "id_certain"]):
                continue
            row = tasks.loc[tid]
            where = f"{label} {tid}"
            assert row["state"] == task.state, where
            assert row["kind"] == task.kind, where
            assert row["mode"] == task.mode, where
            assert row["delegator"] == task.delegator, where
            assert (pd.isna(row["review_required"]) and task.review_required is None) or \
                   row["review_required"] == task.review_required, where
            assert (pd.isna(row["supersedes"]) and not task.supersedes) or \
                   row["supersedes"] == task.supersedes, where
            if bool(row["assignee_certain"]):
                assert row["assignee"] == task.assignee, where
            # The decisions table holds every pass; the coordinator keeps only
            # the current one, which may have no decisions in it yet.
            mine = frame.decisions[frame.decisions["task_id"] == tid]
            if task.review is not None:
                current = mine[mine["review_index"] == row["n_reviews"] - 1]
                assert current.sort_values("seq")["kind"].tolist() == \
                       [d["kind"] for d in task.review.decisions], where
            assert row["n_decisions"] == len(mine), where

        handoffs = frame.handoffs.set_index("handoff_id")
        for hid, handoff in ws.handoffs.items():
            if hid not in handoffs.index or not bool(handoffs.loc[hid, "id_certain"]):
                continue
            row = handoffs.loc[hid]
            assert row["resolution"] == HANDOFF_STATES[handoff.state], f"{label} handoff {hid}"
            assert row["recipient"] == handoff.recipient
            if handoff.state == "accepted":
                assert row["resolved_by"] == handoff.accepted_by
                assert row["n_accepted"] == len(handoff.accepted_task_ids)

        whispers = frame.whispers.set_index("whisper_id")
        for wid, whisper in ws.whispers.items():
            if wid not in whispers.index or not bool(whispers.loc[wid, "id_certain"]):
                continue
            row = whispers.loc[wid]
            assert row["state"] == whisper.state, f"{label} whisper {wid}"
            assert row["question"] == whisper.question
            if whisper.state == "answered":
                assert row["answered_by"] == whisper.answered_by
                assert row["answer"] == (whisper.answer_option or whisper.answer_text)

        delibs = frame.deliberations.set_index("deliberation_id")
        for did, delib in ws.deliberations.items():
            if did not in delibs.index or not bool(delibs.loc[did, "id_certain"]):
                continue
            row = delibs.loc[did]
            assert row["question"] == delib.question, f"{label} deliberation {did}"
            assert row["opener"] == delib.opener
            assert row["n_participants"] == len(delib.participants)
            assert row["n_votes"] == len(delib.votes)
            assert bool(pd.notna(row["closed_at"])) == (delib.state == "closed")


@pytest.mark.parametrize("seed", SEEDS)
def test_the_corrected_artefact_is_reconstructed_as_the_coordinator_computed_it(seed):
    # result is a replay column: the patch applied to the artefact under
    # review. The coordinator stored what it computed, and the two have to
    # agree for every override for the envelope-only read to be handing out
    # the corrected artefact the reviewer produced.
    f = workspace(seed)
    stored = {}
    for art in f.ws.overrides.values():
        stored.setdefault(art.task_id, []).append(art)
    for arts in stored.values():
        arts.sort(key=lambda a: a.ts)
    for label, frame in read_both(f).items():
        for tid, group in frame.overrides.sort_values("seq").groupby("task_id", sort=False):
            arts = stored.get(tid, [])
            assert len(group) == len(arts), f"{label}: override count on {tid}"
            for (_, row), art in zip(group.iterrows(), arts):
                assert row["based_on"] == art.based_on_artefact, f"{label} {tid} based_on"
                assert row["result"] == art.result, f"{label} {tid} result"


@pytest.mark.parametrize("seed", SEEDS)
def test_a_stateful_read_has_nothing_left_to_infer(seed):
    # Server state names every task, so a stateful read is a reading
    # throughout, except where two tasks are indistinguishable; even then,
    # what the row says about them has to be what the coordinator holds.
    f = workspace(seed)
    frame = read_both(f)["state"]
    tasks = frame.tasks.set_index("task_id")
    for tid, task in f.ws.tasks.items():
        assert tasks.loc[tid, "state"] == task.state, tid
        assert tasks.loc[tid, "kind"] == task.kind, tid
        assert tasks.loc[tid, "assignee"] == task.assignee, tid
        assert tasks.loc[tid, "mode"] == task.mode, tid
    assert bool(tasks["assignee_certain"].all())


@pytest.mark.parametrize("seed", SEEDS)
def test_the_invariants_hold_whatever_the_chain_looks_like(seed):
    f = workspace(seed)
    for label, frame in read_both(f).items():
        latency = frame.decisions["latency_s"].dropna()
        assert (latency >= 0).all(), f"{label}: a decision before the review it answers"
        assert frame.overrides["n_ops"].sum() == len(frame.patch_ops), \
            f"{label}: n_ops does not reconcile with patch_ops"
        assert frame.decisions["is_final"].sum() <= len(frame.tasks), \
            f"{label}: more settled reviews than tasks"
        open_work = frame.tasks[~frame.tasks["settled"].fillna(False)]
        assert open_work["settled_at"].isna().all(), f"{label}: settled_at on open work"
        assert open_work["lifetime_s"].isna().all(), f"{label}: a lifetime for running work"
        settled = frame.tasks[frame.tasks["settled"].fillna(False)]
        assert (settled["lifetime_s"].dropna() >= 0).all(), f"{label}: negative lifetime"
        # A coordinator refuses a second vote from the same person and a vote
        # from anyone uninvited, so turnout above one means the votes reached
        # the wrong deliberation.
        vouched = frame.deliberations[frame.deliberations["id_certain"].fillna(False)]
        assert vouched["turnout"].dropna().between(0, 1).all(), \
            f"{label}: turnout above one on a deliberation the read vouches for"
        assert frame.tasks["n_decisions"].fillna(0).sum() == len(frame.decisions), \
            f"{label}: n_decisions does not reconcile"
        assert set(frame.tasks["outcome"]) <= {
            "approved", "overridden", "rejected", "abstained", "escalated", "cancelled",
            "superseded", "completed_after_rejection", "completed_bypassing_review",
            "completed_without_review", "declined", "open"}, f"{label}: an outcome outside the enum"


#: How much of an envelope-only read the projection vouches for, measured over
#: the seed set. A floor rather than a target: falling below it means the
#: pairing has got worse, or an exemption has grown to hide a defect, and
#: either is a regression even though every remaining assertion still passes.
CERTAIN_FLOOR = {"tasks": 0.35, "whispers": 0.45, "deliberations": 0.65, "handoffs": 0.45}


def test_the_envelope_only_read_vouches_for_enough_of_itself():
    counts = {name: [0, 0] for name in CERTAIN_FLOOR}
    for seed in SEEDS:
        frame = read_both(workspace(seed))["envelopes"]
        for name in counts:
            df = frame[name]
            counts[name][0] += int(df["id_certain"].fillna(False).sum())
            counts[name][1] += len(df)
    for name, (certain, total) in counts.items():
        assert total > 0, f"the seed set never produced a {name} row"
        share = certain / total
        assert share >= CERTAIN_FLOOR[name], (
            f"{name}: only {share:.0%} of envelope-only rows are certain, "
            f"floor is {CERTAIN_FLOOR[name]:.0%}")
