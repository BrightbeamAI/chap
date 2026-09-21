"""
Regenerate the pictures in docs/images from the package itself.

    pip install 'chap-analytics[coordinator,viz]'
    python docs/build_images.py

Every chart comes from ``charts.everything`` over a synthetic workspace with
known truth, so the pictures show what the package draws rather than a
mock-up. The ontology diagram is generated from the declared node and edge
types. The report screenshot is taken by hand from ``report.write`` on the
same workspace, because it needs a browser.
"""
from __future__ import annotations

import os
import sys

from chap_analytics import charts, frames, graph, report
from chap_analytics.sample import synthetic

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGES = os.path.join(HERE, "images")

#: The pictures the README and the docs embed, by chart name.
PICTURES = [
    "rate_over_time", "rate_by_reviewer", "refine_reverse", "patch_heatmap", "reliability",
    "survival", "latency_by_reviewer", "promotion", "sequential", "agreement", "whispers",
    "handoffs", "assurance", "cusum", "collaboration", "lineage",
]


def workspace():
    """
    A busy quarter with a story in it: a correction rate near 15% for most of
    it, quorum reviews on a quarter of the tasks, questions and handoffs, one
    reviewer stricter than the others, and a drift upwards late on.
    """
    return frames(synthetic(7, tasks=420, days=70, override_rate=0.15, reject_rate=0.02,
                            refine_share=0.55, quorum_share=0.25, agreement=0.85, whisper_rate=0.15,
                            handoffs=10, open_share=0.04, drift=(340, 0.40),
                            strictness=(0.08, 0.0, -0.06)))


def calibrated_workspace():
    """The same shape of quarter with an agent whose reported confidence tracks its acceptance."""
    return frames(synthetic(11, tasks=420, days=70, outcome_model="calibrated", quorum_share=0.25))


def main(out_dir: str = IMAGES) -> None:
    os.makedirs(out_dir, exist_ok=True)
    f = workspace()
    everything = charts.everything(f)
    everything["reliability"] = charts.everything(calibrated_workspace())["reliability"]
    # The lineage picture: a task two reviewers looked at and one corrected.
    two = f.decisions.groupby("task_id")["reviewer"].nunique()
    corrected = set(f.overrides["task_id"])
    candidates = [t for t in two[two >= 2].index if t in corrected]
    if candidates:
        tid = candidates[0]
        everything["lineage"] = charts.lineage(graph.layout_lanes(graph.lineage_table(f, tid)), tid)
    for name in PICTURES:
        path = os.path.join(out_dir, f"{name}.png")
        everything[name].save(path, scale=2.0)
        print("wrote", path)
    with open(os.path.join(out_dir, "ontology.svg"), "w", encoding="utf-8") as fh:
        fh.write(graph.ontology_svg())
    print("wrote", os.path.join(out_dir, "ontology.svg"))
    build = os.path.join(HERE, "..", "build")
    os.makedirs(build, exist_ok=True)
    path = report.write(f, os.path.join(build, "example_report.html"), title="Synthetic workspace, one quarter")
    print("wrote", path, "(open it in a browser; images/report.png is a screenshot of it)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else IMAGES)
