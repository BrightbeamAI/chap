"""
Every chart is a Vega-Lite specification that compiles, carries its data, and
names the question it answers. Every brief carries a headline with numbers
in it and a decision, and says when its sample is too small.
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

from chap_analytics import Chain, briefs, charts, frames, stats  # noqa: E402
from chap_analytics.sample import support_desk, synthetic  # noqa: E402


@pytest.fixture(scope="module")
def desk():
    return frames(support_desk())


@pytest.fixture(scope="module")
def busy():
    return frames(synthetic(1, tasks=300, quorum_share=0.25, whisper_rate=0.2, handoffs=8, drift=(180, 0.5)))


@pytest.fixture(scope="module")
def empty():
    return frames(Chain(workspace="w", events=[], state=None, source="test"))


# ---------------------------------------------------------------- charts

def test_every_chart_is_a_well_formed_specification_with_its_data(busy):
    all_charts = charts.everything(busy)
    assert len(all_charts) >= 17
    for name, chart in all_charts.items():
        spec = chart.spec
        assert spec["$schema"] == charts.SCHEMA
        assert "mark" in spec or "layer" in spec, name
        assert isinstance(spec["data"]["values"], list), name
        assert spec["data"]["values"], f"{name} has no data on a busy chain"
        assert chart.question and chart.decision, name
        json.dumps(spec)  # nothing pandas or numpy leaks into the spec
        bundle = chart._repr_mimebundle_()
        assert "application/vnd.vegalite.v5+json" in bundle


@pytest.mark.skipif(not charts.node_available(), reason="node is needed to compile the specifications")
def test_every_chart_compiles_and_renders(busy):
    all_charts = charts.everything(busy)
    svgs = charts.render_svg({k: v.spec for k, v in all_charts.items()})
    assert set(svgs) == set(all_charts)
    for name, svg in svgs.items():
        assert svg.startswith("<svg") and len(svg) > 1000, name


@pytest.mark.skipif(not charts.node_available(), reason="node is needed to compile the specifications")
def test_the_collaboration_edges_carry_a_stroke():
    # A shared colour scale across layers once dropped the stroke on every edge.
    f = frames(support_desk())
    svg = charts.render_svg({"c": charts.everything(f)["collaboration"].spec})["c"]
    lines = [seg for seg in svg.split("<line") if 'aria-roledescription="rule mark"' in seg]
    assert lines and all("stroke=" in seg for seg in lines)


def test_empty_chains_give_placeholder_charts(empty):
    for name, chart in charts.everything(empty).items():
        assert chart.spec["data"]["values"] == [], name
        assert chart.spec["mark"]["type"] == "text", name


def test_render_failure_names_the_chart():
    if not charts.node_available():
        pytest.skip("node is needed")
    bad = {"broken": {"$schema": charts.SCHEMA, "data": {"values": []}, "mark": "nonsense"}}
    with pytest.raises(RuntimeError, match="broken"):
        charts.render_svg(bad)


def test_save_writes_svg(busy, tmp_path):
    if not charts.node_available():
        pytest.skip("node is needed")
    c = charts.rate_over_time(stats.rates_over_time(busy, "W"))
    path = c.save(str(tmp_path / "rate.svg"))
    assert open(path, encoding="utf-8").read().startswith("<svg")
    with pytest.raises(ValueError):
        c.save(str(tmp_path / "rate.pdf"))


# ---------------------------------------------------------------- briefs

def test_every_brief_has_numbers_a_decision_and_a_verdict_on_its_sample(desk):
    all_briefs = briefs.everything(desk)
    assert len(all_briefs) == 13
    seen = set()
    for b in all_briefs:
        assert b.name not in seen
        seen.add(b.name)
        assert b.headline and b.decision
        assert isinstance(b.sufficient, bool)
        assert "###" in b.to_markdown() and b.title in str(b)


def test_briefs_read_the_numbers_from_the_chain(desk, busy):
    o = briefs.overrides(desk)
    r = stats.rates(desk).iloc[0]
    assert f"{r['override_rate']:.0%}" in o.headline and str(int(r["n"])) in o.headline
    d = briefs.drift(busy)
    assert d.numbers["alarms"] >= 1 and "Alarm at task" in d.headline
    p = briefs.promotion(busy, threshold=0.10)
    assert "Hold" in p.decision
    calm = briefs.promotion(frames(synthetic(4, tasks=200, override_rate=0.02, reject_rate=0.0)), threshold=0.10)
    assert calm.decision.startswith("Promote")


def test_briefs_say_when_the_sample_is_too_small(empty):
    for b in briefs.everything(empty):
        assert isinstance(b.headline, str)
    small = frames(synthetic(5, tasks=6, open_share=0.0))
    assert not briefs.overrides(small).sufficient
    assert "interval is wide" in briefs.overrides(small).text


def test_agreement_brief_does_not_call_high_agreement_ambiguous(busy):
    a = briefs.agreement(busy)
    assert "agree on nearly every shared pass" in a.decision or "consistently" in a.decision


def test_coverage_and_duties_briefs_flag_an_unwatched_workspace():
    unwatched = frames(synthetic(0, tasks=40, delegator_reviews=True))
    c = briefs.coverage(unwatched)
    assert "no person on the path" in c.text
    d = briefs.duties(unwatched)
    assert "agent_decided" in d.headline.replace(" ", "_") or "agent decided" in d.headline
    assert "Route them to a human" in d.decision


# ------------------------------------------------- transparency-log submissions

def _recorded_submission_chain():
    # A log from a coordinator that recorded audit.submit_to_scitt, as earlier
    # coordinators did: the submission covers the first two positions.
    events = []
    for i in range(3):
        events.append({"seq": i, "arrived": f"2026-01-0{i + 1}T10:00:00Z", "prev_hash": "sha256:" + "0" * 64,
                       "envelope": {"jsonrpc": "2.0", "id": str(i), "method": "task.update",
                                    "params": {"workspace": "w", "from": "human:a",
                                               "ts": f"2026-01-0{i + 1}T10:00:00Z"}}})
    events.append({"seq": 3, "arrived": "2026-01-04T10:00:00Z", "prev_hash": "sha256:" + "0" * 64,
                   "envelope": {"jsonrpc": "2.0", "id": "s", "method": "audit.submit_to_scitt",
                                "params": {"workspace": "w", "from": "service:ops", "ts": "2026-01-04T10:00:00Z",
                                           "range": {"from_seq": 0, "to_seq": 2}}}})
    return frames(Chain(workspace="w", events=events, state=None, source="test"))


def test_a_recorded_submission_is_reported_as_a_share():
    f = _recorded_submission_chain()
    assert "submitted to a transparency log" in briefs.assurance(f).headline
    assert "SCITT submitted" in set(charts.assurance(stats.assurance(f, "D")).data["property"])


def test_a_log_without_a_recorded_submission_says_none_is_on_record():
    # A coordinator from 0.3.0 returns receipts and keeps no submission on the
    # log, so the log cannot show that its entries were submitted.
    from chap_coordinator import Coordinator, CoordinatorOptions
    from chap_analytics import from_coordinator

    c = Coordinator(CoordinatorOptions(scitt_submitter=lambda statement: {"receipt": "r"}))
    send = lambda m, p, a="human:a": c.dispatch({"jsonrpc": "2.0", "id": m, "method": m,  # noqa: E731
                                                  "params": {"workspace": "w", "from": a, **p}})
    send("workspace.create", {"profiles": ["core/1.0", "audit-scitt/1.0"]})
    send("participant.join", {"type": "human"})
    receipts = send("audit.submit_to_scitt", {})["result"]["receipts"]
    assert receipts

    f = frames(from_coordinator(c, workspace="w"))
    assert not f.events["scitt_submitted"].any()
    b = briefs.assurance(f)
    assert "no submission to a transparency log on record" in b.headline
    assert "0% submitted" not in b.headline
    chart = charts.assurance(stats.assurance(f, "D"))
    assert "SCITT submitted" not in set(chart.data["property"])
    assert "no submission" in chart.spec["title"]["subtitle"]
