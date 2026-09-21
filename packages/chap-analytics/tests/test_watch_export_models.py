"""
The watcher reads incrementally and raises each alarm once; the exports carry
what a harness or a prompt review needs; the severity model recovers the
strictness and quality it was generated from, and declines below its minimum.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_analytics import Chain, export, frames, models, watch  # noqa: E402
from chap_analytics.sample import support_desk, support_desk_coordinator, synthetic  # noqa: E402


# ---------------------------------------------------------------- watch

def test_the_watcher_reads_incrementally_and_reports_each_alarm_once(tmp_path):
    chain = synthetic(21, tasks=400, override_rate=0.10, reject_rate=0.0, drift=(200, 0.45),
                      open_share=0.0, envelopes_only=True)
    events = chain.events
    cutoff = {"n": 0}

    def source(from_seq: int):
        return [e for e in events[:cutoff["n"]] if e["seq"] >= from_seq]

    alarms, updates = [], []
    w = watch.Watcher(source, chain.workspace, on_alarm=alarms.append, on_update=updates.append,
                      report_path=str(tmp_path / "live.html"),
                      cusum={"target": 0.10, "shift": 0.20, "false_alarm_runs": 500})
    step = len(events) // 6 + 1
    for i in range(1, 7):
        cutoff["n"] = min(len(events), i * step)
        u = w.poll()
        assert u.total_entries == cutoff["n"]
    assert sum(u.new_entries for u in updates) == len(events)
    assert w.cursor == events[-1]["seq"] + 1
    # A later poll with nothing new adds nothing and repeats no alarm.
    before = len(alarms)
    u = w.poll()
    assert u.new_entries == 0 and len(alarms) == before
    assert alarms, "the drifted workspace should have alarmed"
    assert len({a.task_id for a in alarms}) == len(alarms)
    assert all(a.at_task > 200 for a in alarms)
    assert "moved above its baseline" in alarms[0].headline and alarms[0].window
    assert (tmp_path / "live.html").exists()
    assert w.briefs()[0].name == "overrides"


def test_the_coordinator_source_uses_the_range_cursor():
    coord = support_desk_coordinator()
    fetch = watch.coordinator_source(coord, "wsp_support")
    everything = fetch(0)
    tail = fetch(100)
    assert len(everything) > 100 and len(tail) == len(everything) - 100
    assert tail[0]["seq"] == 100
    w = watch.Watcher(coord, "wsp_support")
    u = w.poll()
    assert u.total_entries == len(everything) and u.frames.tasks.shape[0] > 0


def test_http_source_refuses_other_schemes():
    with pytest.raises(ValueError):
        watch.http_source("file:///etc/passwd", "w")


# ---------------------------------------------------------------- export

def test_evaluation_cases_carry_before_after_and_why(tmp_path):
    f = frames(support_desk())
    cases = export.evaluation_cases(f)
    assert len(cases) == len(f.overrides)
    row = cases.iloc[0]
    assert row["agent_output"] is not None and row["corrected_output"] is not None
    assert row["agent_output"] != row["corrected_output"]
    assert isinstance(row["rationale"], str) and row["rationale"]
    assert row["input"] is not None, "the sample chain carries state, so the input is available"
    path = export.to_jsonl(cases, str(tmp_path / "cases.jsonl"))
    lines = [json.loads(l) for l in open(path, encoding="utf-8")]
    assert len(lines) == len(cases) and set(lines[0]) == set(cases.columns)
    with_approved = export.evaluation_cases(f, include_approved=True)
    assert len(with_approved) == len(cases) + int((f.tasks["outcome"] == "approved").sum())


def test_evaluation_cases_from_envelopes_alone_leave_input_null():
    f = frames(support_desk(envelopes_only=True))
    cases = export.evaluation_cases(f)
    assert len(cases) == len(f.overrides) and cases["input"].isna().all()
    assert cases["agent_output"].notna().all()


def test_prompt_revision_candidates_rank_reversals_first():
    f = frames(synthetic(3, tasks=300, refine_share=0.5))
    c = export.prompt_revision_candidates(f, top=5)
    assert len(c) == 5 and c["priority"].is_monotonic_decreasing
    assert set(c["tag"]) <= {"tone", "substance"}
    substance = c[c["tag"] == "substance"]
    assert (substance["reversing_share"] == 1.0).all()
    assert all(isinstance(e, list) for e in c["examples"])


def test_routing_calibration_finds_a_threshold_when_confidence_means_something():
    good = export.routing_calibration(frames(synthetic(5, tasks=600, outcome_model="calibrated")), target_acceptance=0.85)
    assert good["sufficient"] and good["threshold"] is not None and good["acceptance_above"] >= 0.85
    flat = export.routing_calibration(frames(synthetic(5, tasks=300, override_rate=0.5)), target_acceptance=0.9)
    assert flat["threshold"] is None, "a flat 50% override rate reaches no acceptance target at any confidence"


# ---------------------------------------------------------------- models

def test_the_severity_model_recovers_strict_reviewers_and_weak_agents():
    f = frames(synthetic(2, tasks=600, override_rate=0.25, reject_rate=0.02, strictness=(0.25, 0.0, -0.15),
                         agents=("agent:good", "agent:weak"), agent_quality=(0.15, -0.15)))
    m = models.reviewer_severity(f)
    assert m.attrs["fitted"]
    sev = m[m["role"] == "reviewer"].set_index("unit")["estimate"]
    assert sev["human:ana"] > sev["human:ben"] > sev["human:cal"]
    qual = m[m["role"] == "agent"].set_index("unit")["estimate"]
    assert qual["agent:good"] > 0 > qual["agent:weak"]
    assert (m["se"] > 0).all() and m["n"].sum() == 2 * m.attrs["n"]


def test_the_severity_model_declines_below_its_minimum():
    small = models.reviewer_severity(frames(synthetic(1, tasks=10)))
    assert not small.attrs["fitted"] and "decisions" in small.attrs["reason"] and small.empty
    one_reviewer = models.reviewer_severity(frames(synthetic(1, tasks=100, reviewers=("human:solo",))))
    assert not one_reviewer.attrs["fitted"] and "reviewer" in one_reviewer.attrs["reason"]
    empty = models.reviewer_severity(frames(Chain(workspace="w", events=[], state=None, source="t")))
    assert not empty.attrs["fitted"]
