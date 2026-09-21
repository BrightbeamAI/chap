"""
The standalone report: one file, nothing fetched, and the statistics the
browser recomputes agree with the library's.

The differential test runs the report's own JavaScript under Node on the
tables the report embeds and compares every number with the Python
functions, so a filter in the browser gives the same answer a notebook would.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess

import pytest

pytest.importorskip("pandas")
pytest.importorskip("chap_coordinator")

import pandas as pd  # noqa: E402

from chap_analytics import Chain, charts, frames, graph, report, stats  # noqa: E402
from chap_analytics.sample import support_desk, synthetic  # noqa: E402

RUNNER = os.path.join(os.path.dirname(__file__), "js_stats_runner.js")
needs_node = pytest.mark.skipif(not charts.node_available(), reason="node is needed to run the report's JavaScript")


@pytest.fixture(scope="module")
def busy():
    return frames(synthetic(1, tasks=300, quorum_share=0.25, whisper_rate=0.2, handoffs=8, drift=(180, 0.5)))


@pytest.fixture(scope="module")
def page(busy):
    return report.build(busy)


# ---------------------------------------------------------------- the file

def test_the_report_is_one_self_contained_file(page):
    assert page.startswith("<!DOCTYPE html>")
    # The markup outside the inlined scripts and styles fetches nothing: no
    # external script, stylesheet, image, font or import.
    markup = re.sub(r"<script\b[^>]*>.*?</script>", "<script></script>", page, flags=re.S)
    markup = re.sub(r"<style\b[^>]*>.*?</style>", "<style></style>", markup, flags=re.S)
    assert not re.search(r'<script[^>]+src=', markup)
    assert not re.search(r'<link[^>]+href=', markup)
    assert not re.search(r'<img[^>]+src=', markup)
    assert "http://" not in markup and "https://" not in markup
    styles = re.findall(r"<style\b[^>]*>(.*?)</style>", page, flags=re.S)
    assert all("@import" not in s and "url(http" not in s for s in styles)
    # The runtime and the data are inside.
    assert "vegaEmbed" in page and 'id="chap-data"' in page
    assert "CHAP_STATS" in page
    assert "wsp_synthetic" in page


def test_the_payload_parses_and_carries_no_artefact_content(page, busy):
    m = re.search(r'<script id="chap-data" type="application/json">(.*?)</script>', page, re.S)
    payload = json.loads(m.group(1).replace("<\\/", "</"))
    data = payload["data"]
    assert data["meta"]["counts"]["tasks"] == len(busy.tasks)
    assert len(data["tasks"]) == len(busy.tasks)
    assert set(payload["charts"]) >= {"rate_over_time", "reliability", "collaboration", "lineage", "cusum"}
    assert len(payload["briefs"]) == 13
    for key in ("based_on", "result", "output", "input", "artefact"):
        assert key not in data["tasks"][0] and key not in data["overrides"][0]
    assert len(data["lineages"]) == len(busy.tasks)
    assert data["cusum_h"] and all(r["h"] > 0 for r in data["cusum_h"])


def test_rationales_can_be_left_out(busy):
    page = report.build(busy, rationales=False)
    m = re.search(r'<script id="chap-data" type="application/json">(.*?)</script>', page, re.S)
    payload = json.loads(m.group(1).replace("<\\/", "</"))
    assert all(o["rationale"] is None for o in payload["data"]["overrides"])


def test_write_creates_the_file(busy, tmp_path):
    path = report.write(busy, str(tmp_path / "r.html"), title="A title")
    text = open(path, encoding="utf-8").read()
    assert "<title>A title</title>" in text and os.path.getsize(path) > 500_000


def test_an_empty_chain_still_reports():
    page = report.build(frames(Chain(workspace="w", events=[], state=None, source="test")))
    assert "No decided tasks" in page or "No rows" in page or 'id="chap-data"' in page


# ---------------------------------------------------------------- browser statistics against Python

def _js(data: dict) -> dict:
    proc = subprocess.run(["node", RUNNER], input=json.dumps(data), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _close(a, b, tol=1e-9):
    if a is None or b is None or (isinstance(a, float) and math.isnan(a)) or (isinstance(b, float) and math.isnan(b)):
        return (a is None or (isinstance(a, float) and math.isnan(a))) and (b is None or (isinstance(b, float) and math.isnan(b)))
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))
    return a == b


def _rows(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    return charts._records(df, cols)


def _match(py_rows, js_rows, key, cols, tol=1e-9):
    py_by = {tuple(r[k] for k in key): r for r in py_rows}
    js_by = {tuple(r[k] for k in key): r for r in js_rows}
    assert set(py_by) == set(js_by), (sorted(py_by)[:5], sorted(js_by)[:5])
    for k, pr in py_by.items():
        jr = js_by[k]
        for c in cols:
            assert _close(pr[c], jr.get(c), tol), (k, c, pr[c], jr.get(c))


@needs_node
def test_the_browser_recomputes_what_the_library_computes(busy):
    data = report.embedded_data(busy)
    js = _js(data)
    f = busy

    r = stats.rates(f)
    _match(_rows(r, ["n", "override_rate", "override_low", "override_high", "approve_rate", "reject_rate", "sufficient"]),
           js["rates"], [], ["n", "override_rate", "override_low", "override_high", "approve_rate", "reject_rate", "sufficient"])
    for by, name in (("kind", "rates_by_kind"), ("reviewer", "rates_by_reviewer")):
        _match(_rows(stats.rates(f, by=by), [by, "n", "override_rate", "override_low", "override_high"]),
               js[name], [by], ["n", "override_rate", "override_low", "override_high"])

    ot = stats.rates_over_time(f, "W")
    py_ot = [dict(r, period=r["period"]) for r in _rows(ot, ["period", "n", "override_rate", "override_low", "override_high"])]
    js_ot = [dict(r, period=r["period"].replace(".000Z", "+00:00").replace("Z", "+00:00")) for r in js["rates_over_time"]]
    _match(py_ot, js_ot, ["period"], ["n", "override_rate", "override_low", "override_high"])

    _match(_rows(stats.refine_reverse(f), ["n_overrides", "refining", "reversing", "unstated", "refine_share", "low", "high"]),
           js["refine_reverse"], [], ["n_overrides", "refining", "reversing", "unstated", "refine_share", "low", "high"])
    _match(_rows(stats.refine_reverse(f, by="kind"), ["task_kind", "refining", "reversing", "refine_share"]),
           js["refine_by_kind"], ["task_kind"], ["refining", "reversing", "refine_share"])

    _match(_rows(stats.patch_paths(f), ["task_kind", "top_path", "n", "overrides", "share"]),
           js["patch_paths"], ["task_kind", "top_path"], ["n", "overrides", "share"])

    cal = stats.calibration(f)
    _match(_rows(cal, ["bin", "n", "accepted", "mean_confidence", "acceptance", "low", "high"]),
           js["calibration"]["table"], ["bin"], ["n", "accepted", "mean_confidence", "acceptance", "low", "high"])
    assert _close(cal.attrs["ece"], js["calibration"]["ece"]) and _close(cal.attrs["brier"], js["calibration"]["brier"])

    _match(_rows(stats.survival(f), ["time_s", "at_risk", "events", "censored", "survival", "low", "high"]),
           js["survival"], ["time_s"], ["at_risk", "events", "censored", "survival", "low", "high"])
    lb = stats.latency_by(f, by="reviewer")
    _match(_rows(lb.dropna(subset=["reviewer"]), ["reviewer", "n", "open", "median_s", "p90_s", "mean_s"]),
           [r for r in js["latency_by"] if r["reviewer"]], ["reviewer"], ["n", "open", "median_s", "p90_s", "mean_s"], tol=1e-9)

    _match(_rows(stats.promotion(f, threshold=0.10), ["assignee", "n", "against", "posterior_mean", "low", "high", "p_below_threshold"]),
           js["promotion"], ["assignee"], ["n", "against", "posterior_mean", "low", "high", "p_below_threshold"], tol=1e-7)
    seq = stats.sequential(f, p0=0.10, p1=0.25)
    assert _close(float(seq["llr"].iloc[-1]), js["sequential_last"][0]["llr"], 1e-9)
    assert seq["verdict"].iloc[-1] == js["sequential_last"][0]["verdict"]

    _match(_rows(stats.agreement(f), ["raters", "n_passes", "n_reviewers", "agreement_observed", "agreement_expected", "kappa"]),
           js["agreement"], ["raters"], ["n_passes", "n_reviewers", "agreement_observed", "agreement_expected", "kappa"])
    _match(_rows(stats.pairwise_agreement(f), ["reviewer_a", "reviewer_b", "n_passes", "agreement_observed", "kappa"]),
           js["pairwise"], ["reviewer_a", "reviewer_b"], ["n_passes", "agreement_observed", "kappa"])

    _match(_rows(stats.whispers(f, by="asker"), ["asker", "n", "answered", "lapsed", "pending", "lapse_rate", "low", "high", "median_response_s"]),
           js["whispers"], ["asker"], ["n", "answered", "lapsed", "pending", "lapse_rate", "low", "high", "median_response_s"])
    _match(_rows(stats.handoffs(f, by="recipient"), ["recipient", "n", "accepted", "declined", "open", "accept_rate", "low", "high", "median_response_s"]),
           js["handoffs"], ["recipient"], ["n", "accepted", "declined", "open", "accept_rate", "low", "high", "median_response_s"])

    a = stats.assurance(f, "D")
    py_a = [dict(r) for r in _rows(a, ["period", "n", "chained", "signed", "scitt_submitted"])]
    js_a = [dict(r, period=r["period"].replace(".000Z", "+00:00").replace("Z", "+00:00")) for r in js["assurance"]]
    _match(py_a, js_a, ["period"], ["n", "chained", "signed", "scitt_submitted"])

    cs = stats.cusum(f, false_alarm_runs=1000)
    js_cs = js["cusum"]
    assert len(cs) == len(js_cs)
    assert _close(float(cs["target"].iloc[0]), js_cs[0]["target"])
    for pr, jr in zip(_rows(cs, ["i", "statistic", "changed"]), js_cs):
        assert pr["i"] == jr["i"] and pr["changed"] == jr["changed"] and _close(pr["statistic"], jr["statistic"], 1e-9)

    cov = graph.coverage(f)
    assert cov.attrs["shipped"] == js["coverage"]["shipped"] and cov.attrs["covered"] == js["coverage"]["covered"]
    _match(_rows(graph.concentration(f), ["assignee", "n_decisions", "n_reviewers", "top_reviewer", "top_share", "hhi"]),
           js["concentration"], ["assignee"], ["n_decisions", "n_reviewers", "top_reviewer", "top_share", "hhi"])
    assert len(graph.duties(f)) == len(js["duties"])


@needs_node
def test_the_browser_statistics_agree_on_the_sample_week_too():
    f = frames(support_desk())
    js = _js(report.embedded_data(f))
    r = stats.rates(f).iloc[0]
    assert _close(float(r["override_rate"]), js["rates"][0]["override_rate"])
    cov = graph.coverage(f)
    assert cov.attrs["share"] == js["coverage"]["share"]
    assert len(js["duties"]) == 0
