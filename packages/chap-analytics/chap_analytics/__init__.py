"""
chap-analytics: the record of a collaboration between people and agents,
measured and drawn.

A CHAP audit log records what humans decided about agent work: what an agent
produced, what a person changed, why, and under which rule. This package
projects that log into documented pandas tables, then answers the questions
a team running agents under review asks: how often work is changed and
whether that is falling, which corrections keep recurring, whether reported
confidence means anything, how long review takes, when an agent has earned a
lighter mode, whether reviewers agree, and who is deciding on whose work.

    from chap_analytics import from_sqlite, frames, briefs, report

    chain = from_sqlite("./chap.db", workspace="wsp_support")
    f = frames(chain)

    for b in briefs.everything(f):
        print(b.headline)
    report.write(f, "support.html")

The layers, bottom up:

* ``frames``: eleven documented tables projected from the chain. Every column
  is declared in ``schema.py`` with its dtype and its provenance.
* ``stats``: the estimates with their intervals, and a statement of what
  each one needs before it should be read.
* ``graph``: the chain as a typed graph of tasks, decisions, artefacts and
  people, with lineage, collaboration, coverage and separation of duties.
* ``charts``: one Vega-Lite specification per question, rendered in a
  notebook, saved as SVG or PNG, or embedded in the report.
* ``briefs``: each question answered in a paragraph, with the numbers and
  the decision they support.
* ``report``: one self-contained HTML file that recomputes every statistic
  in the browser as the reader filters.
* ``watch``: a live workspace polled incrementally, with drift alarms.
* ``export``: evaluation cases, prompt-revision candidates and routing
  thresholds in the shapes other tools take.
* ``models``: reviewer severity separated from agent quality.
* ``sample``: a sample week and a synthetic generator with known truth.
"""
from __future__ import annotations

__version__ = "0.2.0"

from . import briefs, charts, export, graph, models, sample, stats, watch  # noqa: E402
from .frames import Frames, frames  # noqa: E402
from .load import (
    Chain,
    from_coordinator,
    from_json,
    from_sqlite,
    from_url,
    redact_artefacts,
)
from .schema import BY_NAME, TABLES, Column, Table, describe  # noqa: E402
from . import report  # noqa: E402

__all__ = [
    "Chain", "Column", "Frames", "Table", "TABLES", "BY_NAME",
    "describe", "frames", "from_coordinator", "from_json", "from_sqlite",
    "from_url", "redact_artefacts", "sample", "stats", "graph", "charts",
    "briefs", "report", "watch", "export", "models", "__version__",
]
