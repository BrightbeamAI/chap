"""A store the TypeScript coordinator wrote reads like one the Python coordinator wrote.

The TypeScript snapshot holds its collections as lists; the Python one holds
them as objects keyed by id. The same workspace, written both ways, has to
project to the same tables.
"""
from __future__ import annotations

import json
import sqlite3

import pandas as pd

from chap_analytics import frames, from_json, from_sqlite
from chap_analytics.sample import support_desk

KEYS = {"tasks": "id", "overrides": "id", "whispers": "id", "deliberations": "id",
        "handoffs": "id", "snapshots": "id", "route_decisions": "id", "members": "uri"}


def as_typescript(state: dict) -> dict:
    """The snapshot as the TypeScript coordinator writes it: collections as lists."""
    out = dict(state)
    for name in KEYS:
        if isinstance(out.get(name), dict):
            out[name] = list(out[name].values())
    return out


def write_store(path, snapshot: dict) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE chap_workspaces (id TEXT PRIMARY KEY, version INTEGER NOT NULL, "
                "data TEXT NOT NULL, updated_at TEXT NOT NULL)")
    con.execute("INSERT INTO chap_workspaces VALUES (?, 1, ?, '2026-01-01T00:00:00Z')",
                (snapshot["id"], json.dumps(snapshot)))
    con.commit()
    con.close()


def test_a_typescript_snapshot_projects_to_the_same_tables(tmp_path):
    chain = support_desk()
    snapshot = {**chain.state, "id": chain.workspace, "audit": chain.events}
    python_store, ts_store = tmp_path / "py.db", tmp_path / "ts.db"
    write_store(python_store, snapshot)
    write_store(ts_store, as_typescript(snapshot))
    assert isinstance(json.loads(sqlite3.connect(ts_store).execute("SELECT data FROM chap_workspaces").fetchone()[0])["tasks"], list)
    a = frames(from_sqlite(str(python_store)))
    b = frames(from_sqlite(str(ts_store)))
    for name in ("tasks", "decisions", "overrides", "events"):
        pd.testing.assert_frame_equal(getattr(a, name), getattr(b, name), check_like=True)
    assert len(b.tasks) == len(a.tasks) > 0


def test_from_json_reads_a_typescript_snapshot(tmp_path):
    chain = support_desk()
    path = tmp_path / "ts.json"
    path.write_text(json.dumps(as_typescript({**chain.state, "id": chain.workspace, "audit": chain.events})))
    b = frames(from_json(str(path)))
    a = frames(chain)
    pd.testing.assert_frame_equal(a.tasks, b.tasks, check_like=True)
