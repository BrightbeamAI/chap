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
