"""
The statistics, checked against workspaces generated from stated rates.

Each generator parameter is a truth the matching statistic has to recover:
an interval that covers the rate it was generated from, a reliability curve
that bends the way the confidence was skewed, a survival median near the
latency the generator drew from, an alarm after the change point and none
before it. Empty chains have to give empty tables rather than exceptions.
"""
from __future__ import annotations

import math

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402

from chap_analytics import Chain, frames, stats  # noqa: E402
from chap_analytics.sample import support_desk, synthetic  # noqa: E402


@pytest.fixture(scope="module")
def fixed():
    return frames(synthetic(0, tasks=400, override_rate=0.30, reject_rate=0.05,
                            refine_share=0.60, quorum_share=0.25, whisper_rate=0.25,
                            lapse_rate=0.30, handoffs=30, handoff_accept=0.80))


@pytest.fixture(scope="module")
def desk():
    return frames(support_desk())


@pytest.fixture(scope="module")
def empty():
    return frames(Chain(workspace="w", events=[], state=None, source="test"))


# ---------------------------------------------------------------- intervals

def test_wilson_matches_known_values():
    low, high = stats.wilson(5, 10)
    assert abs(low - 0.2366) < 1e-3 and abs(high - 0.7634) < 1e-3
    low, high = stats.wilson(0, 10)
    assert low == 0.0 and abs(high - 0.2775) < 1e-3
    assert all(math.isnan(v) for v in stats.wilson(0, 0))
    lows, highs = stats.wilson([1, 9], [10, 10])
    assert lows[0] < lows[1] and highs[0] < highs[1]


def test_beta_cdf_and_quantile_agree_with_closed_forms():
    assert abs(stats.beta_cdf(0.3, 1, 1) - 0.3) < 1e-9          # uniform
    assert abs(stats.beta_cdf(0.5, 2, 3) - 0.6875) < 1e-9       # 1 - (1-x)^4 - 4x(1-x)^3 at 0.5
    assert abs(stats.beta_cdf(0.4, 5, 2) + stats.beta_cdf(0.6, 2, 5) - 1) < 1e-9
    for q in (0.05, 0.5, 0.95):
        x = stats.beta_quantile(q, 3, 7)
        assert abs(stats.beta_cdf(x, 3, 7) - q) < 1e-7


# ---------------------------------------------------------------- rates

def test_rates_recover_the_generating_rates(fixed):
    r = stats.rates(fixed).iloc[0]
    assert r["sufficient"] and r["n"] >= 300
    assert r["override_low"] <= 0.30 <= r["override_high"]
    assert r["reject_low"] <= 0.05 <= r["reject_high"]
    assert abs(r["approve_rate"] + r["override_rate"] + r["reject_rate"] + r["abstain_rate"] - 1) < 1e-9


def test_wilson_intervals_cover_the_truth_at_the_nominal_rate():
    # Twenty independent workspaces; a 95% interval may miss a few. It has to
    # cover the generating rates in most of them, for both a rate and a share.
    covered_rate = covered_share = 0
    for seed in range(20):
        f = frames(synthetic(100 + seed, tasks=200, override_rate=0.30, refine_share=0.60))
        r = stats.rates(f).iloc[0]
        rr = stats.refine_reverse(f).iloc[0]
        covered_rate += r["override_low"] <= 0.30 <= r["override_high"]
        covered_share += rr["low"] <= 0.60 <= rr["high"]
    assert covered_rate >= 17 and covered_share >= 17


def test_rates_by_group_partition_the_decided_tasks(fixed):
    whole = stats.rates(fixed).iloc[0]["n"]
    by_kind = stats.rates(fixed, by="kind")
    assert set(by_kind["kind"]) == {"draft_reply", "summary", "classification"}
    assert by_kind["n"].sum() == whole
    by_reviewer = stats.rates(fixed, by="reviewer")
    assert by_reviewer["n"].sum() == whole


def test_outcomes_cover_every_task_and_rates_refuse_tags(fixed):
    o = stats.outcomes(fixed)
    assert o["n"].sum() == len(fixed.tasks)
    assert abs(o["share"].sum() - 1) < 1e-9
    with pytest.raises(KeyError):
        stats.rates(fixed, by="tag")


def test_rates_over_time_bucket_on_settlement(fixed):
    weekly = stats.rates_over_time(fixed, "W")
    assert weekly["n"].sum() == stats.rates(fixed).iloc[0]["n"]
    assert str(weekly["period"].dt.tz) == "UTC"
    assert weekly["period"].is_monotonic_increasing


def test_refine_share_and_tags_recover_the_split(fixed):
    rr = stats.refine_reverse(fixed).iloc[0]
    assert abs(rr["refine_share"] - 0.60) < 0.12 and rr["low"] < rr["refine_share"] < rr["high"]
    assert rr["unstated"] == 0
    t = stats.tags(fixed)
    assert set(t["tag"]) == {"tone", "substance"}
    tone = t[t["tag"] == "tone"].iloc[0]
    assert tone["refine_share"] == 1.0 and t["n"].sum() == rr["n_overrides"]


# ---------------------------------------------------------------- patch paths

def test_patch_paths_are_shares_of_the_groups_overrides(fixed):
    p = stats.patch_paths(fixed)
    assert (p["share"] <= 1).all() and (p["n"] <= p["overrides"]).all()
    top = stats.patch_paths(fixed, top=1)
    assert len(top) == fixed.overrides["task_kind"].nunique()
    drill = stats.path_rationales(fixed, path=p.iloc[0]["top_path"], kind=p.iloc[0]["task_kind"])
    assert len(drill) == p.iloc[0]["n"] and drill["rationale"].notna().all()


# ---------------------------------------------------------------- calibration

@pytest.mark.parametrize("model, sign", [("calibrated", 0), ("overconfident", -1), ("underconfident", 1)])
def test_calibration_bends_the_way_the_confidence_was_skewed(model, sign):
    f = frames(synthetic(5, tasks=600, outcome_model=model))
    c = stats.calibration(f)
    populated = c[c["n"] >= 30]
    gap = populated["acceptance"] - populated["mean_confidence"]
    if sign == 0:
        assert c.attrs["ece"] < 0.08
    elif sign < 0:
        assert (gap < 0).all() and c.attrs["ece"] > 0.15
    else:
        assert (gap > 0).all() and c.attrs["ece"] > 0.08
    assert c.attrs["sufficient"] and c.attrs["n"] > 500
    assert set(stats.calibration_summary(f)) == {"n", "ece", "brier", "sufficient", "minimum"}


def test_brier_prefers_the_calibrated_agent():
    good = stats.calibration_summary(frames(synthetic(6, tasks=500, outcome_model="calibrated")))
    bad = stats.calibration_summary(frames(synthetic(6, tasks=500, outcome_model="overconfident")))
    assert good["brier"] < bad["brier"]


# ---------------------------------------------------------------- latency

def test_survival_starts_at_one_falls_and_censors_the_open_reviews(fixed):
    lt = stats.latency(fixed)
    assert (~lt["event"]).sum() == len(stats.open_queue(fixed))
    open_reviews = fixed.tasks[(fixed.tasks["outcome"] == "open") & fixed.tasks["was_reviewed"]]
    assert (~lt["event"]).sum() == len(open_reviews)
    s = stats.survival(fixed)
    assert s.iloc[0]["survival"] <= 1.0 and s["survival"].is_monotonic_decreasing
    assert (s["low"] <= s["survival"]).all() and (s["survival"] <= s["high"]).all()
    # The generator draws a reviewer delay log-uniformly on [5, 240] minutes,
    # whose median is sqrt(5 * 240) minutes, on top of whatever queued ahead
    # of the review. The curve has to cross one half at or after that, and
    # with light censoring the crossing is the plain median of the decided
    # passes.
    drawn_median_s = math.sqrt(5 * 240) * 60
    crossing = s[s["survival"] <= 0.5].iloc[0]["time_s"]
    assert drawn_median_s <= crossing < 4 * drawn_median_s
    plain = lt[lt["event"]]["duration_s"].median()
    assert abs(crossing - plain) / plain < 0.15


def test_latency_by_reviewer_counts_every_decided_pass(fixed):
    lb = stats.latency_by(fixed, by="reviewer")
    decided = stats.latency(fixed)
    assert lb["n"].sum() == int(decided["event"].sum())
    assert lb["open"].sum() == int((~decided["event"]).sum())
    named = lb.dropna(subset=["reviewer"])
    assert (named["median_s"] <= named["p90_s"]).all()


# ---------------------------------------------------------------- promotion

def test_promotion_posterior_moves_with_the_threshold(fixed):
    strict = stats.promotion(fixed, threshold=0.05).iloc[0]
    loose = stats.promotion(fixed, threshold=0.40).iloc[0]
    assert strict["p_below_threshold"] < 0.05 < 0.95 < loose["p_below_threshold"]
    assert strict["low"] <= strict["posterior_mean"] <= strict["high"]
    assert strict["sufficient"]
    # Reversing overrides plus rejections: 0.30 * 0.40 + 0.05 of decided tasks.
    assert abs(strict["posterior_mean"] - 0.17) < 0.06


def test_promotion_at_small_n_is_mostly_prior():
    f = frames(synthetic(3, tasks=8, open_share=0.0))
    p = stats.promotion(f, threshold=0.10).iloc[0]
    assert not p["sufficient"] and p["high"] - p["low"] > 0.25


def test_sequential_test_reaches_a_verdict(fixed):
    seq = stats.sequential(fixed, p0=0.05, p1=0.20)
    assert seq["verdict"].iloc[-1] == "hold"
    assert seq["i"].is_monotonic_increasing
    calm = stats.sequential(frames(synthetic(9, tasks=300, override_rate=0.02, reject_rate=0.0)), p0=0.05, p1=0.20)
    assert calm["verdict"].iloc[-1] == "promote"


# ---------------------------------------------------------------- agreement

def test_agreement_uses_multi_reviewer_passes_only(fixed):
    a = stats.agreement(fixed)
    assert list(a["raters"]) == [2]
    assert a.iloc[0]["n_passes"] > 0 and -1 <= a.iloc[0]["kappa"] <= 1
    pairs = stats.pairwise_agreement(fixed)
    assert set(pairs["reviewer_a"]) <= {"human:ana", "human:ben", "human:cal"}
    single = frames(synthetic(2, tasks=60, quorum_share=0.0))
    assert stats.agreement(single).empty and stats.pairwise_agreement(single).empty


def test_vote_agreement_reads_the_deliberation(desk):
    v = stats.vote_agreement(desk)
    assert len(v) == 1 and v.iloc[0]["voters"] == 3 and not v.iloc[0]["sufficient"]


def test_abstentions_are_counted_against_decided(desk):
    ab = stats.abstentions(desk)
    assert len(ab) == 1 and ab.iloc[0]["abstain_category"] == "conflict_of_interest"
    assert 0 < ab.iloc[0]["share"] <= 1


# ---------------------------------------------------------------- whispers, handoffs

def test_whisper_and_handoff_rates_recover_their_truth(fixed):
    w = stats.whispers(fixed).iloc[0]
    assert w["low"] <= 0.30 <= w["high"] and w["n"] >= 60
    h = stats.handoffs(fixed, by=None).iloc[0]
    assert h["n"] == 30 and h["low"] <= 0.80 <= h["high"]


# ---------------------------------------------------------------- assurance

def test_assurance_sees_chaining_signatures_and_submissions():
    events = []
    for i in range(6):
        env = {"jsonrpc": "2.0", "id": str(i), "method": "task.update",
               "params": {"workspace": "w", "from": "human:a", "ts": f"2026-01-0{i + 1}T10:00:00Z"}}
        if i % 2 == 0:
            env["sig"] = "ed25519:k1:AAAA"
        events.append({"seq": i, "arrived": f"2026-01-0{i + 1}T10:00:00Z", "envelope": env,
                       "prev_hash": "sha256:" + "0" * 64})
    events.append({"seq": 6, "arrived": "2026-01-07T10:00:00Z", "prev_hash": "sha256:" + "0" * 64,
                   "envelope": {"jsonrpc": "2.0", "id": "s", "method": "audit.submit_to_scitt",
                                "params": {"workspace": "w", "from": "service:ops",
                                           "ts": "2026-01-07T10:00:00Z",
                                           "range": {"from_seq": 0, "to_seq": 4}}}})
    f = frames(Chain(workspace="w", events=events, state=None, source="test"))
    assert f.events["signed"].sum() == 3
    assert list(f.events["scitt_submitted"]) == [True] * 4 + [False] * 3
    a = stats.assurance(f, "MS").iloc[0]
    assert a["n"] == 7 and a["chained_share"] == 1.0
    assert abs(a["signed_share"] - 3 / 7) < 1e-9 and abs(a["scitt_share"] - 4 / 7) < 1e-9


# ---------------------------------------------------------------- drift

def test_cusum_alarms_after_the_change_and_stays_quiet_before_it():
    f = frames(synthetic(21, tasks=400, override_rate=0.10, reject_rate=0.0,
                         drift=(200, 0.45), open_share=0.0))
    c = stats.cusum(f, target=0.10, shift=0.20, false_alarm_runs=500)
    alarms = c[c["alarm"]]
    assert not alarms.empty
    assert alarms["i"].iloc[0] > 200
    assert (c[c["i"] <= 200]["alarm"] == False).all()  # noqa: E712
    quiet = stats.cusum(frames(synthetic(22, tasks=300, override_rate=0.10, reject_rate=0.0)),
                        target=0.10, shift=0.20, false_alarm_runs=5000)
    assert not quiet["alarm"].any()


# ---------------------------------------------------------------- empty

def test_every_statistic_handles_an_empty_chain(empty):
    for name in stats.__all__:
        if name in ("wilson", "DECIDED"):
            continue
        fn = getattr(stats, name)
        result = fn(empty, 0.1, 0.3) if name == "sequential" else fn(empty)
        if name == "calibration_summary":
            assert result["n"] == 0 and not result["sufficient"]
        else:
            assert isinstance(result, pd.DataFrame) and result.empty, name
