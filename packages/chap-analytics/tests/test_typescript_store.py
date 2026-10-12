"""A store the TypeScript coordinator wrote reads like one the Python coordinator wrote.

The TypeScript snapshot holds its collections as lists; the Python one holds
them as objects keyed by id. The same workspace, written both ways, has to
project to the same tables. So does a store from chap-coordinator 0.3.2 and
later, which writes a row per audit entry and per object.
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
    """The single snapshot table chap-coordinator wrote before 0.3.2."""
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


def write_rows(path, snapshot: dict) -> None:
    """The tables chap-coordinator 0.3.2 and later write, one row per entry and object."""
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE chap_meta (workspace TEXT PRIMARY KEY, data TEXT NOT NULL, "
        "version INTEGER NOT NULL, updated_at TEXT NOT NULL);"
        "CREATE TABLE chap_entries (workspace TEXT NOT NULL, seq INTEGER NOT NULL, "
        "data TEXT NOT NULL, PRIMARY KEY (workspace, seq));"
        "CREATE TABLE chap_objects (workspace TEXT NOT NULL, collection TEXT NOT NULL, "
        "id TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (workspace, collection, id));")
    ws = snapshot["id"]
    meta = {k: v for k, v in snapshot.items() if k not in KEYS and k not in ("audit", "idempotency_keys")}
    con.execute("INSERT INTO chap_meta VALUES (?, ?, ?, '2026-01-01T00:00:00Z')",
                (ws, json.dumps(meta), len(snapshot["audit"])))
    # Out of order on purpose: the reader orders the log by seq.
    for e in reversed(snapshot["audit"]):
        con.execute("INSERT INTO chap_entries VALUES (?, ?, ?)", (ws, e["seq"], json.dumps(e)))
    for name, key in KEYS.items():
        items = snapshot.get(name) or {}
        for item in (items.values() if isinstance(items, dict) else items):
            con.execute("INSERT INTO chap_objects VALUES (?, ?, ?, ?)", (ws, name, item[key], json.dumps(item)))
    con.commit()
    con.close()


def test_a_store_with_a_row_per_entry_projects_to_the_same_tables(tmp_path):
    chain = support_desk()
    snapshot = {**chain.state, "id": chain.workspace, "audit": chain.events}
    single, rows = tmp_path / "single.db", tmp_path / "rows.db"
    write_store(single, snapshot)
    write_rows(rows, snapshot)
    a = frames(from_sqlite(str(single)))
    b = frames(from_sqlite(str(rows)))
    for name in ("tasks", "decisions", "overrides", "events"):
        pd.testing.assert_frame_equal(getattr(a, name), getattr(b, name), check_like=True)
    assert len(b.events) == len(chain.events) > 0


def test_the_coordinator_store_reads_back(tmp_path):
    """A file the Python coordinator's own SqliteStore wrote, end to end."""
    from chap_coordinator.storage.sqlite import SqliteStore
    from chap_coordinator.storage.store import WorkspaceRecord

    chain = support_desk()
    snapshot = {**chain.state, "id": chain.workspace, "audit": chain.events}
    path = tmp_path / "store.db"
    store = SqliteStore(str(path))
    store.save(WorkspaceRecord(id=chain.workspace, data=snapshot, version=1, updated_at="t"))
    store.close()
    a = frames(chain)
    b = frames(from_sqlite(str(path)))
    for name in ("tasks", "decisions", "overrides", "events"):
        pd.testing.assert_frame_equal(getattr(a, name), getattr(b, name), check_like=True)


def test_naming_a_workspace_in_a_store_with_rows(tmp_path):
    chain = support_desk()
    snapshot = {**chain.state, "id": chain.workspace, "audit": chain.events}
    path = tmp_path / "rows.db"
    write_rows(path, snapshot)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO chap_meta VALUES ('wsp_other', '{\"id\": \"wsp_other\"}', 0, 't')")
    con.commit()
    con.close()
    try:
        from_sqlite(str(path))
    except ValueError as exc:
        assert "2 workspaces" in str(exc) and chain.workspace in str(exc)
    else:
        raise AssertionError("two workspaces need a name")
    assert from_sqlite(str(path), workspace="wsp_other").events == []
