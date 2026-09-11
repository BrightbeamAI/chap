"""
chap-analytics: a CHAP chain as tables.

A CHAP audit log records what humans decided about agent work: what an agent
produced, what a person changed, why, and under which rule. This package
projects that log into documented pandas tables so it can be analysed as the
supervision dataset it already is.

    from chap_analytics import from_sqlite, frames

    chain = from_sqlite("./chap.db", workspace="wsp_support")
    f = frames(chain)

    print(f.summary())
    f.overrides.groupby("top_path").size().sort_values(ascending=False)

Ten tables: events, tasks, decisions, overrides, patch_ops, participants,
deliberations, votes, whispers, routing. Every column is declared in
``schema.py`` with its dtype and its provenance, and a column the source could
not populate is present and null rather than absent.

This is stage one of ANALYTICS_ROADMAP.md, and deliberately stops at the
tables. Statistics belong in a layer above, where their assumptions can be
stated, and a chain with twelve decisions in it will not support most of them.
"""
from __future__ import annotations

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
    "Chain",
    "Frames",
    "frames",
    "from_coordinator",
    "from_json",
    "from_sqlite",
    "from_url",
    "redact_artefacts",
    "describe",
    "TABLES",
    "BY_NAME",
    "Table",
    "Column",
    "__version__",
]
