"""
One standalone HTML report for a workspace.

The front page states the headline for each decision in the roadmap's table:
the number, its interval, the sample size, and one line on what it means.
Below it, one section per decision with the chart and the brief, a
collaboration graph, and a lineage view any task on the page opens.

A filter bar at the top applies to everything: date range, task kind, agent,
reviewer, mode and tag. Every chart and every headline recomputes in the
browser, from row-level tables embedded in the file, using the same
statistics as :mod:`chap_analytics.stats` (a test runs the browser code under
Node against the Python results and requires them to agree).

The file is self-contained. The Vega runtime is inlined, the data is inlined,
and nothing is fetched. Artefact content stays out of it; reviewer rationales
are included unless ``rationales=False``.

    from chap_analytics import frames, report
    from chap_analytics.sample import support_desk

    report.write(frames(support_desk()), "support_desk.html")
"""
from __future__ import annotations

import datetime as _dt
import html
import json
import os
from typing import Any

import numpy as np
import pandas as pd

from . import __version__ as _version
from . import briefs as _briefs
from . import charts as _charts
from . import graph as _graph
from . import stats as _stats
from .frames import Frames

__all__ = ["build", "write", "embedded_data"]

_HERE = os.path.dirname(__file__)
_VENDOR = os.path.join(_HERE, "_vendor")
_ASSETS = os.path.join(_HERE, "_report")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _plain_records(df: pd.DataFrame, columns: list[str]) -> list[dict]:
    return _charts._records(df, columns)


def _cusum_lookup(shift: float, false_alarm_runs: int, seed: int = 0) -> list[dict]:
    """Decision intervals for a grid of baseline rates, so the browser can re-run the chart."""
    out = []
    for p0 in np.round(np.arange(0.02, 0.99, 0.02), 2):
        p1 = min(float(p0) + shift, 0.99)
        h = _stats._cusum_threshold(float(p0), p1, false_alarm_runs, runs=400, seed=seed,
                                    horizon=max(200, false_alarm_runs * 3))
        out.append({"p0": float(p0), "h": h})
    return out


def embedded_data(f: Frames, *, threshold: float = 0.10, freq: str = "W", rationales: bool = True,
                  shift: float = 0.10, false_alarm_runs: int = 1000) -> dict[str, Any]:
    """
    The row-level tables the report embeds, with artefact content left out.
    Also what the Node differential test feeds to the browser statistics.
    """
    t = f.tasks.copy()
    settle = _stats._settling_decisions(f)
    reviewer_of = dict(zip(settle["task_id"], settle["reviewer"])) if not settle.empty else {}
    tags_of = dict(zip(settle["task_id"], settle["tags"])) if not settle.empty else {}
    reversing = set(f.overrides[f.overrides["intent_preserved"] == False]["task_id"])  # noqa: E712
    t["reviewer"] = t["task_id"].map(reviewer_of)
    t["tags"] = t["task_id"].map(lambda k: tags_of.get(k, []) if isinstance(tags_of.get(k, []), list) else [])
    t["against"] = t["task_id"].isin(reversing) | (t["outcome"] == "rejected")

    ov = f.overrides.copy()
    if not rationales:
        ov["rationale"] = None
    ops = f.patch_ops.merge(f.tasks[["task_id", "assignee"]], on="task_id", how="left")
    ops = ops.merge(f.overrides[["task_id", "seq", "task_kind"]], on=["task_id", "seq"], how="left")
    passes = _stats.latency(f)
    w = f.whispers.merge(f.tasks[["task_id", "kind"]].rename(columns={"kind": "task_kind"}), on="task_id", how="left")
    edges = _graph.collaboration(f)
    positions = _graph.layout_spring(edges)
    centrality = _graph.centrality(f)

    g = _graph.build(f)
    lineages: dict[str, list[dict]] = {}
    for tid in f.tasks["task_id"]:
        try:
            lanes = _graph.layout_lanes(_graph.lineage_table(g, tid))
        except KeyError:
            continue
        lanes["kind"] = lanes["actor"].map(_graph._kind)
        lanes["when"] = lanes["ts"]
        lanes["text"] = _charts._lineage_text(lanes)
        lineages[tid] = _plain_records(lanes, ["x", "y", "lane", "kind", "action", "node_type", "label", "when", "text"])

    ev = f.events.copy()
    return {
        "meta": {
            "workspace": f.chain.workspace, "source": f.chain.source, "has_state": f.chain.has_state,
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "version": _version, "threshold": threshold, "freq": freq, "shift": shift,
            "false_alarm_runs": false_alarm_runs,
            "counts": {name: int(len(table)) for name, table in f},
            "first": _charts._plain(t["created_at"].min()) if not t.empty else None,
            "last": _charts._plain(pd.concat([t["created_at"], t["settled_at"]]).max()) if not t.empty else None,
        },
        "tasks": _plain_records(t, ["task_id", "kind", "assignee", "delegator", "mode", "outcome", "settled_at",
                                    "created_at", "confidence", "supersedes", "reviewer", "against", "tags",
                                    "criticality", "risk_tier", "was_reviewed", "state"]),
        "decisions": _plain_records(f.decisions, ["task_id", "review_index", "reviewer", "kind", "seq", "ts",
                                                  "latency_s", "is_final", "assignee", "task_kind", "tags"]),
        "overrides": _plain_records(ov, ["task_id", "seq", "reviewer", "ts", "intent_preserved", "top_path",
                                         "task_kind", "assignee", "tags", "policy_refs", "rationale"]),
        "patch_ops": _plain_records(ops, ["task_id", "seq", "top_path", "path", "op", "task_kind", "assignee"]),
        "passes": _plain_records(passes, ["task_id", "review_index", "requested_at", "duration_s", "event",
                                          "reviewer", "task_kind", "assignee"]),
        "whispers": _plain_records(w, ["whisper_id", "task_id", "asker", "answered_by", "state", "lapsed",
                                       "answered", "response_s", "asked_at", "task_kind"]),
        "handoffs": _plain_records(f.handoffs, ["handoff_id", "proposer", "recipient", "resolution", "resolved_by",
                                                "response_s", "proposed_at", "n_tasks"]),
        "events": _plain_records(ev, ["seq", "ts", "method", "actor", "task_id", "chained", "signed", "scitt_submitted"]),
        "edges": _plain_records(edges, ["source", "target", "relation", "weight", "mean_latency_s"]),
        "positions": _plain_records(positions, ["node", "x", "y"]),
        "centrality": _plain_records(centrality, ["participant", "kind", "in_weight", "out_weight", "betweenness"]),
        "lineages": lineages,
        "cusum_h": _cusum_lookup(shift, false_alarm_runs),
    }


def build(f: Frames, *, title: str | None = None, threshold: float = 0.10, freq: str = "W",
          rationales: bool = True) -> str:
    """The report as one HTML string."""
    data = embedded_data(f, threshold=threshold, freq=freq, rationales=rationales)
    charts = _charts.everything(f, threshold=threshold, freq=freq)
    briefs = _briefs.everything(f, threshold=threshold)
    templates = {name: c.spec for name, c in charts.items()}
    questions = {name: {"question": c.question, "decision": c.decision} for name, c in charts.items()}
    brief_rows = [{"name": b.name, "title": b.title, "headline": b.headline, "text": b.text,
                   "decision": b.decision, "sufficient": b.sufficient, "numbers": _charts._plain(b.numbers)}
                  for b in briefs]
    payload = json.dumps({"data": data, "charts": templates, "questions": questions, "briefs": brief_rows},
                         separators=(",", ":"))
    # A closing script tag inside a rationale would end the data block early.
    payload = payload.replace("</", "<\\/")

    heading = html.escape(title or f"CHAP analytics: {f.chain.workspace}")
    period = ""
    if data["meta"]["first"] and data["meta"]["last"]:
        period = f"{data['meta']['first'][:10]} to {data['meta']['last'][:10]}"
    counts = data["meta"]["counts"]

    page = _read(os.path.join(_ASSETS, "report.html"))
    page = page.replace("{{TITLE}}", heading)
    page = page.replace("{{SUBTITLE}}", html.escape(
        f"{counts.get('events', 0)} entries, {counts.get('tasks', 0)} tasks, {counts.get('decisions', 0)} decisions"
        + (f", {period}" if period else "") + f". Read from {f.chain.source}."))
    page = page.replace("{{GENERATED}}", html.escape(data["meta"]["generated_at"]))
    page = page.replace("{{VERSION}}", html.escape(_version))
    page = page.replace("/*{{CSS}}*/", _read(os.path.join(_ASSETS, "report.css")))
    page = page.replace("/*{{VEGA}}*/", _read(os.path.join(_VENDOR, "vega.min.js")))
    page = page.replace("/*{{VEGA_LITE}}*/", _read(os.path.join(_VENDOR, "vega-lite.min.js")))
    page = page.replace("/*{{VEGA_EMBED}}*/", _read(os.path.join(_VENDOR, "vega-embed.min.js")))
    page = page.replace("/*{{STATS}}*/", _read(os.path.join(_ASSETS, "stats.js")))
    page = page.replace("/*{{APP}}*/", _read(os.path.join(_ASSETS, "report.js")))
    page = page.replace("/*{{PAYLOAD}}*/", payload)
    return page


def write(f: Frames, path: str, **kwargs: Any) -> str:
    """Write :func:`build` to ``path`` and return the path."""
    text = build(f, **kwargs)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
