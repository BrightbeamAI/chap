"""
chap-analytics: a CHAP chain as tables.

A CHAP audit log records what humans decided about agent work: what an agent
produced, what a person changed, why, and under which rule. This package
projects that log into documented pandas tables, so it can be analysed as the
supervision dataset it already is.

    from chap_analytics import from_sqlite, frames

    chain = from_sqlite("./chap.db", workspace="wsp_support")
    f = frames(chain)

    print(f.summary())
    f.overrides.groupby("top_path").size().sort_values(ascending=False)

Eleven tables: events, tasks, decisions, overrides, patch_ops, participants,
deliberations, votes, whispers, handoffs, routing. Every column is declared in
``schema.py`` with its dtype and its provenance. A column the source lacked a
value for is present and null.

This is stage one of ANALYTICS_ROADMAP.md, and it stops at the tables.
Statistics belong in a layer above, where their assumptions can be stated.
"""
from __future__ import annotations

from . import sample
from .frames import Frames, frames
from .load import (
    Chain,
    from_coordinator,
    from_json,
    from_sqlite,
    from_url,
    redact_artefacts,
)
from .schema import BY_NAME, TABLES, Column, Table, describe

__version__ = "0.1.0"

__all__ = [
    "Chain", "Column", "Frames", "Table", "TABLES", "BY_NAME",
    "describe", "frames", "from_coordinator", "from_json", "from_sqlite",
    "from_url", "redact_artefacts", "sample", "__version__",
]
