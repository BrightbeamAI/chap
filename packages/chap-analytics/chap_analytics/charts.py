"""
Charts, as Vega-Lite specifications.

Each function takes the frame the matching :mod:`~chap_analytics.stats` or
:mod:`~chap_analytics.graph` function returns and produces a :class:`Chart`:
a Vega-Lite v5 specification with its data inlined. One specification renders
three ways. A notebook shows it inline, since Jupyter renders the Vega-Lite
MIME type natively and :meth:`Chart.altair` hands it to Altair where that is
installed. The report inlines it in one HTML file with the runtime embedded.
:meth:`Chart.save` writes SVG or PNG through ``vl-convert`` for papers and
slides.

Nothing here needs a plotting library. The specifications are plain dicts.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import graph as _graph
from . import stats as _stats
from .frames import Frames

__all__ = [
    "Chart", "rate_over_time", "rates_by", "refine_reverse", "patch_heatmap",
    "reliability", "survival", "latency_by", "promotion", "sequential",
    "agreement", "whispers", "handoffs", "assurance", "cusum", "collaboration",
    "lineage", "everything", "render_svg", "node_available",
]

SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json"

#: One palette for the whole package. Humans are warm, agents cool, services grey.
COLOURS = {
    "primary": "#1f4e79", "band": "#c9d7e6", "accent": "#c0392b", "muted": "#8a8f98",
    "human": "#c0392b", "agent": "#1f4e79", "service": "#8a8f98", "group": "#7d6608",
    "refining": "#2e7d32", "reversing": "#c0392b", "threshold": "#c0392b",
}

_FONT = "Helvetica Neue, Helvetica, Arial, sans-serif"
#: Type sizes, shared by every chart. Large enough to read at the width the
#: README and the report show a chart at; the widths below leave room for them.
FONT_SIZES = {"title": 17, "subtitle": 13, "axis_label": 13, "axis_title": 14, "legend": 13, "mark": 13}
_CONFIG = {
    "font": _FONT,
    "axis": {"labelFontSize": FONT_SIZES["axis_label"], "titleFontSize": FONT_SIZES["axis_title"],
             "gridColor": "#eeeeee", "domainColor": "#cccccc", "labelPadding": 6, "titlePadding": 10},
    "legend": {"labelFontSize": FONT_SIZES["legend"], "titleFontSize": FONT_SIZES["legend"], "symbolSize": 120},
    "title": {"fontSize": FONT_SIZES["title"], "anchor": "start", "fontWeight": 600,
              "subtitleFontSize": FONT_SIZES["subtitle"], "subtitleColor": "#555555", "subtitlePadding": 6,
              "offset": 14},
    "view": {"stroke": None},
}


@dataclass
class Chart:
    """A Vega-Lite specification and the question it answers."""
    spec: dict[str, Any]
    name: str
    question: str = ""
    decision: str = ""
    data: pd.DataFrame | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """The Vega-Lite specification, with its data inlined."""
        return self.spec

    def to_json(self, **kwargs: Any) -> str:
        """The specification as JSON text; keyword arguments go to ``json.dumps``."""
        return json.dumps(self.spec, **kwargs)

    def altair(self):
        """The same chart as an ``altair.Chart``. Needs altair installed."""
        try:
            import altair as alt
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("altair() needs altair: pip install 'chap-analytics[viz]'") from exc
        return alt.Chart.from_dict(self.spec)

    def save(self, path: str, *, scale: float = 2.0) -> str:
        """
        Write the chart as SVG or PNG, by file extension.

        SVG is written through ``vl-convert-python`` where it is installed
        (the ``viz`` extra) and otherwise through Node with the runtime this
        package ships. PNG needs ``vl-convert-python``.
        """
        lower = path.lower()
        if lower.endswith(".png"):
            with open(path, "wb") as fh:
                fh.write(self.png(scale=scale))
            return path
        if lower.endswith(".svg"):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.svg())
            return path
        raise ValueError("save() writes .svg or .png")

    def png(self, *, scale: float = 2.0) -> bytes:
        """The chart as PNG bytes. Needs ``vl-convert-python`` (the ``viz`` extra)."""
        try:
            import vl_convert as vlc
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("PNG needs vl-convert-python: pip install 'chap-analytics[viz]'") from exc
        return vlc.vegalite_to_png(self.to_json(), scale=scale)

    def svg(self) -> _graph.SvgText:
        """
        The chart as SVG text, through ``vl-convert`` where installed and
        otherwise the shipped runtime under Node. A notebook displays the
        result as a picture.
        """
        try:
            import vl_convert as vlc
            return _graph.SvgText(vlc.vegalite_to_svg(self.to_json()))
        except ImportError:
            return _graph.SvgText(render_svg({self.name: self.spec})[self.name])

    def _repr_mimebundle_(self, include=None, exclude=None):
        # The Vega-Lite bundle is what a notebook with a Vega renderer draws.
        # Where vl-convert is installed an SVG rides along, so a viewer without
        # one (a notebook on GitHub, for instance) still shows the picture.
        data = {"application/vnd.vegalite.v5+json": self.spec,
                "text/plain": f"<Chart {self.name}: {self.question}>"}
        try:
            import vl_convert as vlc
        except ImportError:
            return data
        try:
            data["image/svg+xml"] = vlc.vegalite_to_svg(self.to_json())
        except Exception:  # noqa: BLE001 - a rendering fault leaves the Vega-Lite bundle, which still draws
            return data
        return data


# ============================================================================
#   Rendering through the shipped runtime
# ============================================================================

_VENDOR = os.path.join(os.path.dirname(__file__), "_vendor")


def node_available() -> bool:
    """Whether a ``node`` binary is on the path, which SVG rendering without vl-convert needs."""
    return shutil.which("node") is not None


def render_svg(specs: dict[str, dict[str, Any]], *, timeout: int = 120) -> dict[str, str]:
    """
    Render Vega-Lite specifications to SVG text with the vendored runtime,
    under Node. Returns ``{name: svg}``. Raises ``RuntimeError`` naming the
    first specification that failed to compile, so a test can use it as a
    check that every chart is well formed.
    """
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("render_svg needs a node binary on the path, or install vl-convert-python")
    proc = subprocess.run([node, os.path.join(_VENDOR, "render.js")], input=json.dumps(specs),
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"render.js failed: {proc.stderr[:500]}")
    results = json.loads(proc.stdout)
    bad = {k: v["error"] for k, v in results.items() if not v.get("ok")}
    if bad:
        name, err = next(iter(bad.items()))
        raise RuntimeError(f"chart {name!r} failed to compile: {err}")
    return {k: v["svg"] for k, v in results.items()}


# ============================================================================
#   Helpers
# ============================================================================

def _plain(v: Any) -> Any:
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.isoformat()
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return None if np.isnan(v) else float(v)
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NaT or v is pd.NA:
        return None
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    return v


def _records(df: pd.DataFrame, columns: list[str] | None = None) -> list[dict]:
    cols = [c for c in (columns or list(df.columns)) if c in df.columns]
    out = []
    for row in df[cols].itertuples(index=False):
        out.append({c: _plain(v) for c, v in zip(cols, row)})
    return out


def _base(name: str, title: str, subtitle: str | None, data: list[dict], **kw: Any) -> dict:
    spec: dict[str, Any] = {
        "$schema": SCHEMA,
        "title": {"text": title, **({"subtitle": subtitle} if subtitle else {})},
        "data": {"values": data},
        "config": _CONFIG,
        "usermeta": {"chap_analytics": {"chart": name}},
    }
    spec.update(kw)
    return spec


def _pct() -> dict:
    """Axis settings for a share drawn as a percentage."""
    return {"format": ".0%"}


def _empty(name: str, title: str, question: str, decision: str, note: str) -> Chart:
    spec = _base(name, title, note, [], mark={"type": "text", "text": note, "fontSize": FONT_SIZES["mark"],
                                                "color": COLOURS["muted"]}, width=520, height=70)
    return Chart(spec, name, question, decision)


# ============================================================================
#   Rates and the refine / reverse split
# ============================================================================

def rate_over_time(over_time: pd.DataFrame, rate: str = "override", *, title: str | None = None) -> Chart:
    """
    A rate per period with its interval as a band. Takes the frame from
    :func:`chap_analytics.stats.rates_over_time`.
    """
    name = "rate_over_time"
    q = "How often do reviewers change the agent's output, and is that moving?"
    d = "A rising band is a prompt or model change to look at; a wide band is a period with too few decisions to read."
    if over_time.empty:
        return _empty(name, f"{rate.capitalize()} rate over time", q, d, "No decided tasks in this chain yet.")
    df = over_time.rename(columns={f"{rate}_rate": "rate", f"{rate}_low": "low", f"{rate}_high": "high"})
    data = _records(df, ["period", "n", "rate", "low", "high", "sufficient"])
    spec = _base(name, title or f"{rate.capitalize()} rate over time",
                 "Share of decided tasks per period, with a 95% Wilson interval", data,
                 width=620, height=260,
                 encoding={"x": {"field": "period", "type": "temporal", "title": None,
                                 "axis": {"format": "%-d %b", "labelOverlap": True, "labelAngle": 0}}},
                 layer=[
                     {"mark": {"type": "area", "color": COLOURS["band"], "opacity": 0.6},
                      "encoding": {"y": {"field": "low", "type": "quantitative", "axis": _pct(),
                                         "title": f"{rate} rate", "scale": {"domain": [0, 1]}},
                                   "y2": {"field": "high"}}},
                     {"mark": {"type": "line", "color": COLOURS["primary"], "point": True},
                      "encoding": {"y": {"field": "rate", "type": "quantitative"},
                                   "tooltip": [{"field": "period", "type": "temporal", "title": "period"},
                                               {"field": "n", "title": "decided"},
                                               {"field": "rate", "format": ".1%", "title": rate},
                                               {"field": "low", "format": ".1%"},
                                               {"field": "high", "format": ".1%"}]}},
                 ])
    return Chart(spec, name, q, d, df)


def rates_by(by_group: pd.DataFrame, by: str, rate: str = "override", *, title: str | None = None) -> Chart:
    """A rate per group as bars with interval whiskers. Takes :func:`stats.rates` output."""
    name = f"rate_by_{by}"
    q = f"Does the {rate} rate differ by {by}?"
    d = "Groups whose whiskers sit apart differ; groups whose whiskers overlap are within noise at this sample."
    if by_group.empty:
        return _empty(name, f"{rate.capitalize()} rate by {by}", q, d, "No decided tasks in this chain yet.")
    df = by_group.rename(columns={f"{rate}_rate": "rate", f"{rate}_low": "low", f"{rate}_high": "high"})
    data = _records(df, [by, "n", "rate", "low", "high", "sufficient"])
    spec = _base(name, title or f"{rate.capitalize()} rate by {by}",
                 "Bars are the observed share; whiskers the 95% Wilson interval", data,
                 width=520, height={"step": 32},
                 encoding={"y": {"field": by, "type": "nominal", "sort": "-x", "title": None}},
                 layer=[
                     {"mark": {"type": "bar", "color": COLOURS["primary"]},
                      "encoding": {"x": {"field": "rate", "type": "quantitative", "axis": _pct(),
                                         "scale": {"domain": [0, 1]}, "title": f"{rate} rate"},
                                   "opacity": {"condition": {"test": "datum.sufficient", "value": 1}, "value": 0.45},
                                   "tooltip": [{"field": by}, {"field": "n", "title": "decided"},
                                               {"field": "rate", "format": ".1%"},
                                               {"field": "low", "format": ".1%"}, {"field": "high", "format": ".1%"}]}},
                     {"mark": {"type": "rule", "color": "#333333"},
                      "encoding": {"x": {"field": "low", "type": "quantitative"}, "x2": {"field": "high"}}},
                 ])
    return Chart(spec, name, q, d, df)


def refine_reverse(split: pd.DataFrame, by: str | None = None) -> Chart:
    """Refining against reversing overrides, per group. Takes :func:`stats.refine_reverse` output."""
    name = "refine_reverse"
    q = "When reviewers correct the agent, are they refining its decision or reversing it?"
    d = "A high refining share points at the prompt or the wording; a high reversing share points at the policy or the task context."
    if split.empty:
        return _empty(name, "Refining against reversing", q, d, "No overrides in this chain yet.")
    df = split.copy()
    key = None
    for candidate in (by, f"task_{by}" if by else None):
        if candidate and candidate in df.columns:
            key = candidate
            break
    if key is None:
        df["group"] = "all overrides"
        key = "group"
    long = pd.concat([
        df[[key, "refining"]].rename(columns={"refining": "count"}).assign(category="refining"),
        df[[key, "reversing"]].rename(columns={"reversing": "count"}).assign(category="reversing"),
        df[[key, "unstated"]].rename(columns={"unstated": "count"}).assign(category="unstated"),
    ])
    data = _records(long, [key, "category", "count"])
    spec = _base(name, "Refining against reversing",
                 "From intent_preserved on each override: refined the agent's decision, or reversed it", data,
                 width=520, height={"step": 32},
                 mark="bar",
                 encoding={
                     "y": {"field": key, "type": "nominal", "title": None},
                     "x": {"field": "count", "type": "quantitative", "stack": "normalize",
                           "axis": {"format": ".0%"}, "title": "share of overrides"},
                     "color": {"field": "category", "type": "nominal", "title": None,
                               "scale": {"domain": ["refining", "reversing", "unstated"],
                                         "range": [COLOURS["refining"], COLOURS["reversing"], COLOURS["muted"]]}},
                     "tooltip": [{"field": key}, {"field": "category"}, {"field": "count"}],
                 })
    return Chart(spec, name, q, d, long)


# ============================================================================
#   Patch paths
# ============================================================================

def patch_heatmap(paths: pd.DataFrame, by: str = "task_kind") -> Chart:
    """Which part of the artefact gets corrected, per group. Takes :func:`stats.patch_paths` output."""
    name = "patch_heatmap"
    q = "Where in the artefact do the corrections land?"
    d = "The darkest cell names the field and the task kind to fix first. Click through to the rationales behind it."
    if paths.empty:
        return _empty(name, "Where corrections land", q, d, "No overrides in this chain yet.")
    key = by if by in paths.columns else None
    df = paths.copy()
    if key is None:
        df["group"] = "all"
        key = "group"
    depth = "top_path" if "top_path" in df.columns else "path"
    data = _records(df, [key, depth, "n", "overrides", "share"])
    spec = _base(name, "Where corrections land",
                 "Share of the group's overrides that touched each part of the artefact", data,
                 width={"step": 84}, height={"step": 36},
                 encoding={"x": {"field": key, "type": "nominal", "title": None},
                           "y": {"field": depth, "type": "nominal", "title": None}},
                 layer=[
                     {"mark": "rect",
                      "encoding": {"color": {"field": "share", "type": "quantitative",
                                             "scale": {"scheme": "blues", "domain": [0, 1]},
                                             "legend": {"format": ".0%", "title": "share"}},
                                   "tooltip": [{"field": key}, {"field": depth, "title": "path"},
                                               {"field": "n", "title": "overrides touching it"},
                                               {"field": "overrides", "title": "overrides in group"},
                                               {"field": "share", "format": ".0%"}]}},
                     {"mark": {"type": "text", "fontSize": FONT_SIZES["mark"]},
                      "encoding": {"text": {"field": "n"},
                                   "color": {"condition": {"test": "datum.share > 0.5", "value": "white"},
                                             "value": "#222222"}}},
                 ])
    return Chart(spec, name, q, d, df)


# ============================================================================
#   Calibration
# ============================================================================

def reliability(cal: pd.DataFrame) -> Chart:
    """Reliability diagram. Takes :func:`stats.calibration` output."""
    name = "reliability"
    q = "Does the agent's reported confidence track how often reviewers accept its work?"
    d = "Points below the diagonal are overconfidence: lower the routing threshold there. Above it, the agent undersells itself."
    ece = cal.attrs.get("ece", float("nan"))
    n = cal.attrs.get("n", 0)
    df = cal[cal["n"] >= 5] if not cal.empty else cal
    if df.empty:
        return _empty(name, "Reliability", q, d, "Fewer than five decided tasks with a reported confidence in any bin.")
    data = _records(df, ["bin", "mean_confidence", "acceptance", "low", "high", "n"])
    sub = (f"{n} decided tasks with a confidence; expected calibration error {ece:.3f}; "
           "bins with fewer than five tasks are left out") if n else ""
    spec = _base(name, "Reliability: reported confidence against acceptance", sub, data,
                 width=380, height=380,
                 layer=[
                     {"data": {"values": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
                      "mark": {"type": "line", "strokeDash": [4, 4], "color": COLOURS["muted"]},
                      "encoding": {"x": {"field": "x", "type": "quantitative"},
                                   "y": {"field": "y", "type": "quantitative"}}},
                     {"mark": {"type": "rule", "color": "#555555"},
                      "encoding": {"x": {"field": "mean_confidence", "type": "quantitative"},
                                   "y": {"field": "low", "type": "quantitative"},
                                   "y2": {"field": "high"}}},
                     {"mark": {"type": "line", "color": COLOURS["primary"]},
                      "encoding": {"x": {"field": "mean_confidence", "type": "quantitative"},
                                   "y": {"field": "acceptance", "type": "quantitative"}}},
                     {"mark": {"type": "point", "filled": True, "color": COLOURS["primary"]},
                      "encoding": {"x": {"field": "mean_confidence", "type": "quantitative",
                                         "scale": {"domain": [0, 1]}, "axis": {"format": ".0%"},
                                         "title": "reported confidence"},
                                   "y": {"field": "acceptance", "type": "quantitative",
                                         "scale": {"domain": [0, 1]}, "axis": {"format": ".0%"},
                                         "title": "accepted as drafted"},
                                   "size": {"field": "n", "type": "quantitative", "legend": {"title": "tasks"},
                                            "scale": {"zero": False, "range": [60, 600]}},
                                   "tooltip": [{"field": "mean_confidence", "format": ".2f", "title": "confidence"},
                                               {"field": "acceptance", "format": ".1%"},
                                               {"field": "n", "title": "tasks"}]}},
                 ])
    return Chart(spec, name, q, d, df)


# ============================================================================
#   Time to decision
# ============================================================================

def survival(surv: pd.DataFrame, by: str | None = None, *, unit: str = "h") -> Chart:
    """Kaplan-Meier curve of reviews still waiting. Takes :func:`stats.survival` output."""
    name = "survival"
    q = "How long does a review wait for its first decision, and how much is still waiting?"
    d = "A curve that flattens above zero is a queue that does not clear: add reviewers or reroute that kind of work."
    if surv.empty:
        return _empty(name, "Reviews still waiting", q, d, "No review passes in this chain yet.")
    div = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    df = surv.assign(time=surv["time_s"] / div)
    cols = ["time", "survival", "low", "high", "at_risk", "events", "censored"] + ([by] if by else [])
    data = _records(df, cols)
    enc_colour = {"color": {"field": by, "type": "nominal", "title": by}} if by else {}
    spec = _base(name, "Reviews still waiting for a first decision",
                 "Kaplan-Meier estimate; open reviews are censored at the end of the chain", data,
                 width=620, height=280,
                 encoding={"x": {"field": "time", "type": "quantitative",
                                 "title": {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]}},
                 layer=[
                     *([] if by else [{"mark": {"type": "area", "color": COLOURS["band"], "opacity": 0.6,
                                                "interpolate": "step-after"},
                                       "encoding": {"y": {"field": "low", "type": "quantitative"},
                                                    "y2": {"field": "high"}}}]),
                     {"mark": {"type": "line", "interpolate": "step-after",
                               **({} if by else {"color": COLOURS["primary"]})},
                      "encoding": {"y": {"field": "survival", "type": "quantitative",
                                         "scale": {"domain": [0, 1]}, "axis": {"format": ".0%"},
                                         "title": "still waiting"},
                                   **enc_colour,
                                   "tooltip": [{"field": "time", "format": ".2f", "title": unit},
                                               {"field": "survival", "format": ".1%", "title": "waiting"},
                                               {"field": "at_risk"}, {"field": "events"}, {"field": "censored"}]}},
                 ])
    return Chart(spec, name, q, d, df)


def latency_by(lat: pd.DataFrame, by: str = "reviewer", *, unit: str = "h") -> Chart:
    """Median and 90th percentile time to decision per group. Takes :func:`stats.latency_by` output."""
    name = f"latency_by_{by}"
    q = f"Who is the work waiting on, by {by}?"
    d = "A long median is load; a long 90th percentile with a short median is a few stuck items. They want different fixes."
    if lat.empty:
        return _empty(name, f"Time to decision by {by}", q, d, "No decided passes in this chain yet.")
    div = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    df = lat.dropna(subset=[by]).assign(median=lat["median_s"] / div, p90=lat["p90_s"] / div)
    data = _records(df, [by, "n", "open", "median", "p90", "sufficient"])
    label = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[unit]
    spec = _base(name, f"Time to first decision by {by}", f"Bar: median. Tick: 90th percentile. In {label}.",
                 data, width=520, height={"step": 32},
                 encoding={"y": {"field": by, "type": "nominal", "sort": "-x", "title": None}},
                 layer=[
                     {"mark": {"type": "bar", "color": COLOURS["primary"]},
                      "encoding": {"x": {"field": "median", "type": "quantitative", "title": label},
                                   "opacity": {"condition": {"test": "datum.sufficient", "value": 1}, "value": 0.45},
                                   "tooltip": [{"field": by}, {"field": "n", "title": "decided"},
                                               {"field": "open"}, {"field": "median", "format": ".2f"},
                                               {"field": "p90", "format": ".2f"}]}},
                     {"mark": {"type": "tick", "color": COLOURS["accent"], "thickness": 2},
                      "encoding": {"x": {"field": "p90", "type": "quantitative"}}},
                 ])
    return Chart(spec, name, q, d, df)


# ============================================================================
#   Promotion
# ============================================================================

def promotion(post: pd.DataFrame, *, points: int = 200) -> Chart:
    """
    The posterior over each agent's correction rate against the threshold.
    Takes :func:`stats.promotion` output; the density is drawn from the
    posterior's own ``alpha`` and ``beta`` on each row, so the prior the
    caller chose is the prior the picture shows.
    """
    name = "promotion"
    q = "Is this agent's rate of substantive correction below the bar, given what has been seen?"
    d = "Promote when nearly all of the curve sits left of the line. A wide curve says the answer is still mostly prior."
    if post.empty:
        return _empty(name, "Promotion readiness", q, d, "No decided tasks in this chain yet.")
    key = next((c for c in post.columns if c not in (
        "n", "against", "rate", "posterior_mean", "low", "high", "p_below_threshold", "threshold",
        "alpha", "beta", "sufficient")), None)
    rows = []
    xs = np.linspace(0.001, 0.999, points)
    for r in post.itertuples(index=False):
        n = int(r.n)
        a, b = float(r.alpha), float(r.beta)
        lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        dens = np.exp(lb + (a - 1) * np.log(xs) + (b - 1) * np.log(1 - xs))
        label = getattr(r, key) if key else "all"
        for x, y in zip(xs, dens):
            rows.append({"group": label, "rate": float(x), "density": float(y),
                         "p_below": float(r.p_below_threshold), "n": n})
    df = pd.DataFrame(rows)
    threshold = float(post["threshold"].iloc[0])
    spec = _base(name, "Promotion readiness: where the true correction rate sits",
                 f"Posterior density per agent; the line is the threshold {threshold:.0%}", _records(df),
                 width=620, height=260,
                 layer=[
                     {"mark": {"type": "area", "opacity": 0.35, "line": True},
                      "encoding": {"x": {"field": "rate", "type": "quantitative", "axis": {"format": ".0%"},
                                         "title": "substantive correction rate", "scale": {"domain": [0, 1]}},
                                   "y": {"field": "density", "type": "quantitative", "title": None, "axis": None},
                                   "color": {"field": "group", "type": "nominal", "title": None},
                                   "tooltip": [{"field": "group"}, {"field": "n", "title": "decided"},
                                               {"field": "p_below", "format": ".1%", "title": "P(below threshold)"}]}},
                     {"data": {"values": [{"t": threshold}]},
                      "mark": {"type": "rule", "color": COLOURS["threshold"], "strokeDash": [6, 3], "size": 2},
                      "encoding": {"x": {"field": "t", "type": "quantitative"}}},
                 ])
    return Chart(spec, name, q, d, post)


def sequential(seq: pd.DataFrame) -> Chart:
    """The sequential test's running log-likelihood ratio between its bounds. Takes :func:`stats.sequential` output."""
    name = "sequential"
    q = "Has enough been seen to promote or to hold?"
    d = "Crossing the lower bound says promote; the upper says hold. Between them, keep collecting."
    if seq.empty:
        return _empty(name, "Sequential test", q, d, "No decided tasks in this chain yet.")
    key = next((c for c in seq.columns if c not in (
        "task_id", "settled_at", "i", "against", "llr", "upper", "lower", "verdict")), None)
    df = seq.copy()
    if key is None:
        df["group"] = "all"
        key = "group"
    # The test stops at its first crossing; the line is drawn up to that point
    # and the verdict is written there. What came after it did not count.
    stops = []
    df["after"] = False
    for g, part in df.groupby(key, dropna=False):
        part = part.sort_values("i")
        reached = part[part["verdict"] != "continue"]
        if not reached.empty:
            first = reached.iloc[0]
            stops.append({key: g, "i": int(first["i"]), "llr": float(first["llr"]),
                          "text": f"{first['verdict']} at task {int(first['i'])}"})
            df.loc[(df[key] == g) & (df["i"] > first["i"]), "after"] = True
    data = _records(df, ["i", "llr", key, "verdict", "settled_at", "after"])
    up, lo = float(seq["upper"].iloc[0]), float(seq["lower"].iloc[0])
    spec = _base(name, "Sequential probability ratio test", "Log-likelihood ratio per decided task, in time order",
                 data, width=620, height=260,
                 layer=[
                     {"data": {"values": [{"y": up, "label": "hold"}, {"y": lo, "label": "promote"}]},
                      "mark": {"type": "rule", "strokeDash": [6, 3], "color": COLOURS["muted"]},
                      "encoding": {"y": {"field": "y", "type": "quantitative"}}},
                     {"transform": [{"filter": "!datum.after"}],
                      "mark": {"type": "line"},
                      "encoding": {"x": {"field": "i", "type": "quantitative", "title": "decided tasks"},
                                   "y": {"field": "llr", "type": "quantitative", "title": "log-likelihood ratio"},
                                   "color": {"field": key, "type": "nominal", "title": None},
                                   "tooltip": [{"field": key}, {"field": "i"}, {"field": "llr", "format": ".2f"},
                                               {"field": "verdict"}]}},
                     {"data": {"values": stops},
                      "mark": {"type": "point", "filled": True, "size": 90, "color": COLOURS["accent"]},
                      "encoding": {"x": {"field": "i", "type": "quantitative"},
                                   "y": {"field": "llr", "type": "quantitative"},
                                   "tooltip": [{"field": key}, {"field": "text", "title": "verdict"}]}},
                     {"data": {"values": stops},
                      "mark": {"type": "text", "dx": 8, "dy": -12, "align": "left", "fontSize": FONT_SIZES["mark"], "color": COLOURS["accent"]},
                      "encoding": {"x": {"field": "i", "type": "quantitative"},
                                   "y": {"field": "llr", "type": "quantitative"},
                                   "text": {"field": "text"}}},
                 ])
    return Chart(spec, name, q, d, df)


# ============================================================================
#   Agreement
# ============================================================================

def agreement(pairs: pd.DataFrame) -> Chart:
    """Cohen's kappa per reviewer pair as a matrix. Takes :func:`stats.pairwise_agreement` output."""
    name = "agreement"
    q = "Do reviewers agree with each other on the same artefact?"
    d = "A pair with low kappa on many shared passes points at an ambiguous policy or a difference in standard worth talking about."
    if pairs.empty:
        return _empty(name, "Reviewer agreement", q, d, "No review pass had two reviewers decide it.")
    both = pd.concat([pairs, pairs.rename(columns={"reviewer_a": "reviewer_b", "reviewer_b": "reviewer_a"})])
    data = _records(both, ["reviewer_a", "reviewer_b", "n_passes", "kappa", "agreement_observed", "sufficient"])
    spec = _base(name, "Reviewer agreement (Cohen's kappa)", "On passes both reviewers decided; accept against change",
                 data, width={"step": 92}, height={"step": 36},
                 encoding={"x": {"field": "reviewer_a", "type": "nominal", "title": None},
                           "y": {"field": "reviewer_b", "type": "nominal", "title": None}},
                 layer=[
                     {"mark": "rect",
                      "encoding": {"color": {"field": "kappa", "type": "quantitative",
                                             "scale": {"scheme": "redblue", "domain": [-1, 1]},
                                             "legend": {"title": "kappa"}},
                                   "tooltip": [{"field": "reviewer_a"}, {"field": "reviewer_b"},
                                               {"field": "n_passes"}, {"field": "kappa", "format": ".2f"},
                                               {"field": "agreement_observed", "format": ".0%", "title": "agreed"}]}},
                     {"mark": {"type": "text", "fontSize": FONT_SIZES["mark"]},
                      "encoding": {"text": {"field": "n_passes"}}},
                 ])
    return Chart(spec, name, q, d, both)


# ============================================================================
#   Whispers, handoffs, assurance
# ============================================================================

def whispers(w: pd.DataFrame, by: str = "asker") -> Chart:
    """Lapse rate per asker with intervals. Takes :func:`stats.whispers` output."""
    name = "whispers"
    q = "How often do agents have to ask, and does anyone answer in time?"
    d = "A high lapse rate is a question the task input should have answered, or a reviewer who is never at the desk."
    if w.empty:
        return _empty(name, "Whispers that lapsed", q, d, "No whispers in this chain.")
    key = by if by in w.columns else None
    df = w.copy()
    if key is None:
        df["group"] = "all"
        key = "group"
    data = _records(df, [key, "n", "answered", "lapsed", "pending", "lapse_rate", "low", "high", "sufficient"])
    spec = _base(name, "Whispers that lapsed", "Share of resolved whispers whose deadline passed unanswered",
                 data, width=520, height={"step": 32},
                 encoding={"y": {"field": key, "type": "nominal", "title": None}},
                 layer=[
                     {"mark": {"type": "bar", "color": COLOURS["primary"]},
                      "encoding": {"x": {"field": "lapse_rate", "type": "quantitative", "axis": {"format": ".0%"},
                                         "scale": {"domain": [0, 1]}, "title": "lapse rate"},
                                   "tooltip": [{"field": key}, {"field": "n", "title": "asked"}, {"field": "answered"},
                                               {"field": "lapsed"}, {"field": "pending"},
                                               {"field": "lapse_rate", "format": ".0%"}]}},
                     {"mark": {"type": "rule", "color": "#333333"},
                      "encoding": {"x": {"field": "low", "type": "quantitative"}, "x2": {"field": "high"}}},
                 ])
    return Chart(spec, name, q, d, df)


def handoffs(h: pd.DataFrame, by: str = "recipient") -> Chart:
    """Acceptance per recipient with intervals. Takes :func:`stats.handoffs` output."""
    name = "handoffs"
    q = "Are handoffs accepted, and by whom?"
    d = "A recipient who declines often, or takes long to answer, is a gap in the shift plan."
    if h.empty:
        return _empty(name, "Handoffs accepted", q, d, "No handoffs in this chain.")
    key = by if by in h.columns else None
    df = h.copy()
    if key is None:
        df["group"] = "all"
        key = "group"
    data = _records(df, [key, "n", "accepted", "declined", "open", "accept_rate", "low", "high", "median_response_s"])
    spec = _base(name, "Handoffs accepted", "Share of resolved handoffs the recipient accepted", data,
                 width=520, height={"step": 32},
                 encoding={"y": {"field": key, "type": "nominal", "title": None}},
                 layer=[
                     {"mark": {"type": "bar", "color": COLOURS["primary"]},
                      "encoding": {"x": {"field": "accept_rate", "type": "quantitative", "axis": {"format": ".0%"},
                                         "scale": {"domain": [0, 1]}, "title": "acceptance"},
                                   "tooltip": [{"field": key}, {"field": "n", "title": "proposed"}, {"field": "accepted"},
                                               {"field": "declined"}, {"field": "open"},
                                               {"field": "median_response_s", "title": "median response (s)"}]}},
                     {"mark": {"type": "rule", "color": "#333333"},
                      "encoding": {"x": {"field": "low", "type": "quantitative"}, "x2": {"field": "high"}}},
                 ])
    return Chart(spec, name, q, d, df)


def assurance(a: pd.DataFrame) -> Chart:
    """Chained, signed and submitted shares per period. Takes :func:`stats.assurance` output."""
    name = "assurance"
    q = "Is the record verifiable end to end?"
    d = "A line at 100% means every entry in the period carries that property. A dip is a period whose entries carry less than the rest."
    if a.empty:
        return _empty(name, "Chain assurance", q, d, "No entries in this chain.")
    long = pd.concat([
        a[["period", "n", "chained_share"]].rename(columns={"chained_share": "share"}).assign(property="hash-linked"),
        a[["period", "n", "signed_share"]].rename(columns={"signed_share": "share"}).assign(property="signed"),
        a[["period", "n", "scitt_share"]].rename(columns={"scitt_share": "share"}).assign(property="SCITT submitted"),
    ])
    data = _records(long, ["period", "n", "share", "property"])
    spec = _base(name, "Chain assurance", "Share of entries per period that are hash-linked, signed, and submitted to a transparency log",
                 data, width=620, height=240,
                 mark={"type": "line", "point": True, "interpolate": "monotone"},
                 encoding={"x": {"field": "period", "type": "temporal", "title": None,
                                 "axis": {"format": "%-d %b", "labelOverlap": True, "labelAngle": 0}},
                           "y": {"field": "share", "type": "quantitative", "axis": {"format": ".0%"},
                                 "scale": {"domain": [0, 1]}, "title": "share of entries"},
                           "color": {"field": "property", "type": "nominal", "title": None},
                           "tooltip": [{"field": "period", "type": "temporal"}, {"field": "property"},
                                       {"field": "n", "title": "entries"}, {"field": "share", "format": ".0%"}]})
    return Chart(spec, name, q, d, long)


# ============================================================================
#   Drift
# ============================================================================

def cusum(c: pd.DataFrame) -> Chart:
    """The CUSUM statistic against its threshold with alarms marked. Takes :func:`stats.cusum` output."""
    name = "cusum"
    q = "Has the correction rate moved since the last prompt or model change?"
    d = "An alarm names the task at which the accumulated evidence crossed the line. Look at what changed just before it."
    if c.empty:
        return _empty(name, "Drift (CUSUM)", q, d, "No decided tasks in this chain yet.")
    key = next((col for col in c.columns if col not in (
        "task_id", "settled_at", "i", "changed", "rate_so_far", "statistic", "threshold", "alarm", "target", "detect")), None)
    df = c.copy()
    if key is None:
        df["group"] = "all"
        key = "group"
    data = _records(df, ["i", "statistic", "threshold", "alarm", "rate_so_far", key, "settled_at", "task_id"])
    target, detect = float(c["target"].iloc[0]), float(c["detect"].iloc[0])
    spec = _base(name, "Drift in the correction rate (CUSUM)",
                 f"Tuned to a move from {target:.0%} to {detect:.0%}; the dashed line is the decision interval",
                 data, width=620, height=260,
                 layer=[
                     {"mark": {"type": "rule", "strokeDash": [6, 3], "color": COLOURS["threshold"]},
                      "encoding": {"y": {"field": "threshold", "type": "quantitative"}}},
                     {"mark": {"type": "line", "color": COLOURS["primary"]},
                      "encoding": {"x": {"field": "i", "type": "quantitative", "title": "decided tasks"},
                                   "y": {"field": "statistic", "type": "quantitative", "title": "CUSUM"},
                                   "detail": {"field": key}}},
                     {"transform": [{"filter": "datum.alarm"}],
                      "mark": {"type": "point", "filled": True, "color": COLOURS["accent"], "size": 80},
                      "encoding": {"x": {"field": "i", "type": "quantitative"},
                                   "y": {"field": "statistic", "type": "quantitative"},
                                   "tooltip": [{"field": "i", "title": "task no."}, {"field": "task_id"},
                                               {"field": "settled_at", "type": "temporal"},
                                               {"field": "rate_so_far", "format": ".1%"}]}},
                 ])
    return Chart(spec, name, q, d, df)


# ============================================================================
#   Graphs
# ============================================================================

#: The collaboration graph's frame: circle area in square pixels for the
#: smallest and the largest participant, the plotting height, and the data
#: range both axes span. The report's page script uses the same numbers.
_NODE_AREA = (200.0, 2200.0)
_GRAPH_HEIGHT = 440
_GRAPH_DOMAIN = (-0.08, 1.08)


def _label_positions(y: pd.Series, size: pd.Series, size_max: float) -> pd.Series:
    """
    Where a participant's name sits: above its circle by the circle's radius
    under the size scale plus a margin, expressed in the y axis's own units
    so the text mark can be placed with the same scale as the circle.
    """
    share = size.fillna(0) / size_max if size_max > 0 else size * 0
    area = _NODE_AREA[0] + (_NODE_AREA[1] - _NODE_AREA[0]) * share
    pixels = np.sqrt(area / math.pi) + 10
    per_pixel = (_GRAPH_DOMAIN[1] - _GRAPH_DOMAIN[0]) / _GRAPH_HEIGHT
    return y + pixels * per_pixel


def collaboration(edges: pd.DataFrame, positions: pd.DataFrame | None = None, *,
                  centrality: pd.DataFrame | None = None) -> Chart:
    """
    The collaboration graph: participants as circles, work between them as
    lines weighted by count. Takes :func:`graph.collaboration` output and,
    optionally, :func:`graph.layout_spring` positions and :func:`graph.centrality`.
    """
    name = "collaboration"
    q = "Who does most of the reviewing, and does one person decide most of one agent's work?"
    d = "A thick line into one agent from one reviewer is concentration. A node every line passes through is a bottleneck."
    if edges.empty:
        return _empty(name, "Collaboration graph", q, d, "No work passed between participants in this chain.")
    pos = positions if positions is not None else _graph.layout_spring(edges)
    pos = pos.set_index("node")
    e = edges.copy()
    e["x"] = e["source"].map(pos["x"])
    e["y"] = e["source"].map(pos["y"])
    e["x2"] = e["target"].map(pos["x"])
    e["y2"] = e["target"].map(pos["y"])
    nodes = pos.reset_index()
    nodes["kind"] = nodes["node"].map(_graph._kind)
    if centrality is not None and not centrality.empty:
        nodes = nodes.merge(centrality[["participant", "in_weight", "out_weight", "betweenness"]],
                            left_on="node", right_on="participant", how="left")
    else:
        nodes["in_weight"] = nodes["node"].map(e.groupby("target")["weight"].sum()).fillna(0)
        nodes["out_weight"] = nodes["node"].map(e.groupby("source")["weight"].sum()).fillna(0)
        nodes["betweenness"] = None
    nodes["size"] = nodes["in_weight"].fillna(0) + nodes["out_weight"].fillna(0)
    size_max = float(nodes["size"].max()) if len(nodes) else 1.0
    nodes["label_y"] = _label_positions(nodes["y"], nodes["size"], size_max)
    spec = _base(name, "Collaboration graph", "Lines are work between participants, thicker for more; circles are people, agents and services",
                 _records(e, ["source", "target", "relation", "weight", "x", "y", "x2", "y2", "mean_latency_s"]),
                 width=620, height=_GRAPH_HEIGHT,
                 resolve={"scale": {"color": "independent", "strokeWidth": "independent", "size": "independent"}},
                 layer=[
                     {"mark": {"type": "rule", "opacity": 0.55},
                      "encoding": {"x": {"field": "x", "type": "quantitative", "axis": None, "scale": {"domain": list(_GRAPH_DOMAIN)}},
                                   "y": {"field": "y", "type": "quantitative", "axis": None, "scale": {"domain": list(_GRAPH_DOMAIN)}},
                                   "x2": {"field": "x2"}, "y2": {"field": "y2"},
                                   "strokeWidth": {"field": "weight", "type": "quantitative", "scale": {"range": [1, 14]},
                                                   "legend": {"title": "items"}},
                                   "color": {"field": "relation", "type": "nominal", "title": None},
                                   "tooltip": [{"field": "source"}, {"field": "target"}, {"field": "relation"},
                                               {"field": "weight"}, {"field": "mean_latency_s", "title": "mean latency (s)", "format": ".0f"}]}},
                     {"data": {"values": _records(nodes, ["node", "kind", "x", "y", "size", "in_weight", "out_weight", "betweenness"])},
                      "mark": {"type": "circle", "opacity": 0.95, "stroke": "white", "strokeWidth": 1.5},
                      "encoding": {"x": {"field": "x", "type": "quantitative"}, "y": {"field": "y", "type": "quantitative"},
                                   "size": {"field": "size", "type": "quantitative",
                                            "scale": {"domain": [0, size_max], "range": list(_NODE_AREA)}, "legend": None},
                                   "color": {"field": "kind", "type": "nominal", "title": None,
                                             "scale": {"domain": ["human", "agent", "service", "group"],
                                                       "range": [COLOURS["human"], COLOURS["agent"], COLOURS["service"], COLOURS["group"]]}},
                                   "tooltip": [{"field": "node"}, {"field": "kind"}, {"field": "in_weight", "title": "work received"},
                                               {"field": "out_weight", "title": "work sent"}, {"field": "betweenness", "format": ".2f"}]}},
                     {"data": {"values": _records(nodes, ["node", "x", "label_y"])},
                      "mark": {"type": "text", "fontSize": FONT_SIZES["mark"], "fontWeight": 600},
                      "encoding": {"x": {"field": "x", "type": "quantitative"}, "y": {"field": "label_y", "type": "quantitative"},
                                   "text": {"field": "node"}}},
                 ])
    return Chart(spec, name, q, d, e)


def _lineage_text(df: pd.DataFrame) -> pd.Series:
    """The word written over each point: the action, or for a decision its kind (approved, overrode, rejected, abstained)."""
    kinds = {"approve": "approved", "override": "overrode", "reject": "rejected", "abstain": "abstained"}
    label_kind = df["label"].astype(str).str.split(" by ").str[0].map(lambda k: kinds.get(k, k))
    return pd.Series(np.where((df["node_type"] == "decision") & (df["action"] == "decided"), label_kind, df["action"]),
                     index=df.index)


def lineage(lanes: pd.DataFrame, task_id: str | None = None) -> Chart:
    """
    The lineage of one task as a swimlane: one lane per participant, one
    point per action, in time order. Takes :func:`graph.layout_lanes` output.
    """
    name = "lineage"
    q = "What led to this outcome, and who was involved at each step?"
    d = "Read left to right. Every point is an envelope on the chain. If no human lane appears, no person acted on this task."
    if lanes.empty:
        return _empty(name, "Lineage", q, d, "This task has no recorded history.")
    df = lanes.copy()
    df["kind"] = df["actor"].map(_graph._kind)
    df["when"] = df["ts"]
    df["text"] = _lineage_text(df)
    data = _records(df, ["x", "y", "lane", "kind", "action", "node_type", "label", "when", "text"])
    title = f"Lineage of {task_id}" if task_id else "Lineage"
    spec = _base(name, title, "One lane per participant; each point is an action recorded on the chain, in time order",
                 data, width=max(420, 76 * len(df) + 60), height={"step": 52},
                 encoding={"x": {"field": "x", "type": "ordinal", "axis": None},
                           "y": {"field": "lane", "type": "nominal", "title": None,
                                 "sort": {"field": "y", "op": "min"}}},
                 layer=[
                     {"mark": {"type": "line", "color": "#bbbbbb", "strokeWidth": 1, "interpolate": "monotone"},
                      "encoding": {"detail": {"field": "node"}, "order": {"field": "x"}}},
                     {"mark": {"type": "circle", "size": 260, "stroke": "white", "strokeWidth": 1.5},
                      "encoding": {"color": {"field": "kind", "type": "nominal", "title": None,
                                             "scale": {"domain": ["human", "agent", "service", "group"],
                                                       "range": [COLOURS["human"], COLOURS["agent"], COLOURS["service"], COLOURS["group"]]}},
                                   "tooltip": [{"field": "when", "type": "temporal", "title": "when"}, {"field": "lane", "title": "who"},
                                               {"field": "action"}, {"field": "node_type"}, {"field": "label"}]}},
                     {"mark": {"type": "text", "dy": -21, "fontSize": FONT_SIZES["mark"], "color": "#333333"},
                      "encoding": {"text": {"field": "text"}}},
                 ])
    return Chart(spec, name, q, d, df)


# ============================================================================
#   All of them
# ============================================================================

def everything(f: Frames, *, threshold: float = 0.10, freq: str = "W") -> dict[str, Chart]:
    """
    Every chart the decision table names, computed from one ``Frames``. The
    report and the notebook build from this.
    """
    charts: dict[str, Chart] = {}
    charts["rate_over_time"] = rate_over_time(_stats.rates_over_time(f, freq))
    charts["rate_by_kind"] = rates_by(_stats.rates(f, by="kind"), "kind")
    charts["rate_by_reviewer"] = rates_by(_stats.rates(f, by="reviewer"), "reviewer")
    charts["refine_reverse"] = refine_reverse(_stats.refine_reverse(f, by="kind"), "kind")
    charts["patch_heatmap"] = patch_heatmap(_stats.patch_paths(f))
    charts["reliability"] = reliability(_stats.calibration(f))
    charts["survival"] = survival(_stats.survival(f))
    charts["latency_by_reviewer"] = latency_by(_stats.latency_by(f, by="reviewer"), "reviewer")
    charts["promotion"] = promotion(_stats.promotion(f, threshold=threshold))
    charts["sequential"] = sequential(_stats.sequential(f, p0=threshold, p1=min(0.99, threshold + 0.15)))
    charts["agreement"] = agreement(_stats.pairwise_agreement(f))
    charts["whispers"] = whispers(_stats.whispers(f))
    charts["handoffs"] = handoffs(_stats.handoffs(f))
    charts["assurance"] = assurance(_stats.assurance(f, "D"))
    charts["cusum"] = cusum(_stats.cusum(f))
    edges = _graph.collaboration(f)
    charts["collaboration"] = collaboration(edges, _graph.layout_spring(edges), centrality=_graph.centrality(f))
    if not f.tasks.empty:
        # The most recently settled task, or the last created where none has settled.
        settled = f.tasks.dropna(subset=["settled_at"]).sort_values("settled_at")
        tid = (settled if not settled.empty else f.tasks.sort_values("created_at"))["task_id"].iloc[-1]
        charts["lineage"] = lineage(_graph.layout_lanes(_graph.lineage_table(f, tid)), tid)
    return charts
