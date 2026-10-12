"""
Each call writes its own audit entries and the objects it changed, so the
cost of a write does not grow with the log. Before 0.3.2 every call wrote
the whole workspace, and a long run slowed with the square of its length.

The mirror of this file is
packages/coordinator/tests/store_append_only.test.ts.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.coordinator import _rehydrate_workspace
from chap_coordinator.storage.sqlite import SqliteStore
from chap_coordinator.storage.store import (
    MemoryStore, StoreChange, StoreConflictError,
)

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "handoff/1.0",
            "whisper/1.0", "deliberation/1.0", "routing/1.0"]
OPTIONS = dict(deterministic_ids=True, deterministic_clock=True,
               enable_chain=True, default_profiles=PROFILES)
COLLECTIONS = ("members", "tasks", "overrides", "whispers", "deliberations",
               "handoffs", "snapshots", "route_decisions")


class Recording(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.changes: list[StoreChange] = []

    def apply(self, change: StoreChange) -> None:
        self.changes.append(StoreChange(**json.loads(json.dumps(change.__dict__))))
        super().apply(change)


class Flaky(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.failures = 0

    def apply(self, change: StoreChange) -> None:
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("disk full")
        super().apply(change)


def coordinator(store=None, **extra) -> Coordinator:
    return Coordinator(CoordinatorOptions(**OPTIONS, store=store, **extra))


def driver(c: Coordinator, workspace: str = "w"):
    n = [0]

    def send(method: str, sender: str, **params) -> dict:
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
                           "params": {"workspace": workspace, "from": sender, **params}})
    return send


def ok(reply: dict) -> dict:
    assert "result" in reply, reply.get("error")
    return reply["result"]


def exercise(c: Coordinator) -> None:
    """A workspace that has used every collection a store keeps."""
    send = driver(c)
    ok(send("workspace.create", "human:a", profiles=PROFILES))
    for who, kind in (("human:a", "human"), ("human:c", "human"),
                      ("agent:b", "agent"), ("agent:d", "agent")):
        ok(send("participant.join", who, type=kind))
    first = ok(send("task.create", "human:a", kind="draft", input={"n": 1},
                    assignee="agent:b", idempotency_key="k-1"))["task_id"]
    ok(send("task.update", "agent:b", task_id=first, state="in_progress"))
    ok(send("review.request", "agent:b", task_id=first, artefact={"text": "draft"},
            to=["human:a"], rule="any_one_approves"))
    ok(send("decide.override", "human:a", task_id=first, rationale="tone",
            diff=[{"op": "replace", "path": "/text", "value": "final"}], tags=["tone"]))
    second = ok(send("task.create", "human:a", kind="draft", input={"n": 2},
                     assignee="agent:d"))["task_id"]
    ok(send("review.request", "agent:d", task_id=second, artefact={"text": "x"}, to=["human:c"]))
    ok(send("decide.reject", "human:c", task_id=second, comment="no", request_revision=True))
    third = ok(send("task.create", "human:a", kind="plan", input={}, assignee="agent:b"))["task_id"]
    late = ok(send("whisper.ask", "agent:b", task_id=third, to=["human:a"], question="now?",
                   deadline_ms=0, default_if_lapsed="no"))["whisper_id"]
    asked = ok(send("whisper.ask", "agent:b", task_id=third, to=["human:c"], question="later?",
                    deadline_ms=600_000, default_if_lapsed="no"))["whisper_id"]
    ok(send("whisper.answer", "human:c", whisper_id=asked, answer="yes"))
    assert len(c.check_whisper_lapses("w", "2100-01-01T00:00:00.000Z")) == 1
    assert c.workspaces["w"].whispers[late].state == "lapsed"
    delib = ok(send("deliberate.open", "human:a", to=["human:a", "human:c"],
                    rule="any_one_approves", question="ship?", task_id=third))["deliberation_id"]
    ok(send("deliberate.vote", "human:c", deliberation_id=delib, vote="yea"))
    ok(send("deliberate.close", "human:a", deliberation_id=delib))
    handoff = ok(send("handoff.propose", "agent:b", to="human:c",
                      tasks=[{"task_id": third}]))["handoff_id"]
    ok(send("handoff.accept", "human:c", handoff_id=handoff))
    fourth = ok(send("task.create", "human:a", kind="route", input={}, assignee="agent:d"))["task_id"]
    ok(send("task.route", "human:a", task_id=fourth, candidates=["agent:b", "agent:d"]))
    snap = ok(send("control.snapshot", "human:a"))["snapshot_artefact_id"]
    ok(send("control.set_mode_ceiling", "human:a", new_ceiling="trial", reason="hold"))
    ok(send("control.rollback", "human:a", to_snapshot_artefact_id=snap))
    ok(send("control.pause", "human:a", task_id=fourth, reason="hold"))
    ok(send("control.resume", "human:a", task_id=fourth))
    ok(send("participant.leave", "agent:d"))
    ws = c.workspaces["w"]
    for name in COLLECTIONS:
        assert getattr(ws, name), f"{name} is empty"
    assert list(ws.idempotency_keys) == ["k-1"]


def plain(c: Coordinator) -> dict:
    out = {}
    for ws_id, ws in c.workspaces.items():
        data = asdict(ws)
        data["audit"] = [e.to_dict() for e in ws.audit]
        out[ws_id] = json.loads(json.dumps(data))
    return out


# ---- writes ---------------------------------------------------------


def test_each_write_carries_the_calls_entries_and_objects_at_a_flat_size():
    store = Recording()
    c = coordinator(store)
    send = driver(c)
    ok(send("workspace.create", "human:a"))
    ok(send("participant.join", "human:a", type="human"))
    ok(send("participant.join", "agent:b", type="agent"))
    version = store.changes[-1].version
    sizes = []
    for k in range(200):
        store.changes.clear()
        tid = ok(send("task.create", "human:a", kind="k", input={"k": k},
                      assignee="agent:b"))["task_id"]
        ok(send("task.complete", "agent:b", task_id=tid, output={"k": k}))
        assert len(store.changes) == 2
        for change in store.changes:
            assert change.base == version
            assert len(change.entries) == 1
            assert change.replace is False
            assert len(change.objects.get("tasks", {})) <= 1
            version = change.version
        sizes.append(sum(len(json.dumps(ch.__dict__)) for ch in store.changes))
    # The two-hundredth task is written in the same space as the hundredth,
    # whose numbers have as many digits.
    assert sizes[199] == sizes[100]


def test_a_restart_brings_back_every_collection_as_it_was():
    store = MemoryStore()
    c = coordinator(store)
    exercise(c)
    restarted = coordinator(store)
    assert plain(restarted) == plain(c)
    r = restarted.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                            "params": {"workspace": "w", "from": "human:a"}})
    assert r["result"]["ok"] is True


def test_a_lapsed_whisper_is_written_though_the_check_runs_outside_a_call():
    store = MemoryStore()
    c = coordinator(store)
    send = driver(c)
    ok(send("workspace.create", "human:a", profiles=PROFILES))
    ok(send("participant.join", "human:a", type="human"))
    ok(send("participant.join", "agent:b", type="agent"))
    task = ok(send("task.create", "human:a", kind="k", input={}, assignee="agent:b"))["task_id"]
    wid = ok(send("whisper.ask", "agent:b", task_id=task, to=["human:a"], question="q",
                  deadline_ms=0, default_if_lapsed="no"))["whisper_id"]
    c.check_whisper_lapses("w", "2100-01-01T00:00:00.000Z")
    restarted = coordinator(store).workspaces["w"]
    assert restarted.whispers[wid].state == "lapsed"
    assert len(restarted.audit) == len(c.workspaces["w"].audit)


def test_a_workspace_put_in_place_directly_is_written_whole_on_its_next_entry():
    source = coordinator()
    exercise(source)
    store = MemoryStore()
    c = coordinator(store)
    c.workspaces["w"] = _rehydrate_workspace(asdict(source.workspaces["w"]))
    ok(driver(c)("participant.join", "human:e", type="human"))
    assert plain(coordinator(store)) == plain(c)


# ---- objects the caller holds -----------------------------------------


def test_objects_the_caller_holds_are_not_shared_with_the_workspace():
    store = MemoryStore()
    c = coordinator(store)
    send = driver(c)
    ok(send("workspace.create", "human:a", profiles=PROFILES))
    for who, kind in (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent")):
        ok(send("participant.join", who, type=kind))
    t1 = ok(send("task.create", "human:a", kind="k", input={}, assignee="agent:b"))["task_id"]
    t2 = ok(send("task.create", "human:a", kind="k", input={}, assignee="agent:b"))["task_id"]
    reviewers = ["human:a"]
    ok(send("review.request", "agent:b", task_id=t1, artefact={"x": 1}, to=reviewers))
    ok(send("review.request", "agent:b", task_id=t2, artefact={"x": 2}, to=reviewers))
    # Widening the second review leaves the first as it was.
    ok(send("review.request", "agent:b", task_id=t2, artefact={"x": 2}, to=["human:c"]))
    ws = c.workspaces["w"]
    assert ws.tasks[t1].review.requested_to == ["human:a"]
    assert reviewers == ["human:a"]
    # Changing a result changes nothing in the workspace.
    described = ok(send("workspace.describe", "human:a"))
    described["profiles"].append("not/1.0")
    assert "not/1.0" not in ws.profiles
    assert plain(coordinator(store)) == plain(c)


def test_a_collection_an_application_replaced_is_tracked_again():
    store, errors = MemoryStore(), []
    c = coordinator(store, on_store_error=lambda e, r: errors.append(e))
    send = driver(c)
    ok(send("workspace.create", "human:a"))
    ok(send("participant.join", "human:a", type="human"))
    ws = c.workspaces["w"]
    ws.tasks = dict(ws.tasks)
    for _ in range(3):
        ok(send("task.create", "human:a", kind="k", input={}, assignee="human:a"))
    assert errors == []
    assert plain(coordinator(store)) == plain(c)


# ---- failed writes --------------------------------------------------


def test_the_next_write_carries_what_a_failed_one_did_not():
    store, seen = Flaky(), []
    c = coordinator(store, on_store_error=lambda err, rec: seen.append(rec))
    send = driver(c)
    ok(send("workspace.create", "human:a"))
    ok(send("participant.join", "human:a", type="human"))
    store.failures = 2
    ok(send("participant.join", "agent:b", type="agent"))
    ok(send("task.create", "human:a", kind="k", input={}, assignee="agent:b"))
    assert len(seen) == 2
    ok(send("participant.join", "human:c", type="human"))
    assert plain(coordinator(store)) == plain(c)


def test_with_no_on_store_error_a_failed_write_is_logged(caplog):
    store = Flaky()
    c = coordinator(store)
    send = driver(c)
    ok(send("workspace.create", "human:a"))
    store.failures = 1
    with caplog.at_level(logging.ERROR, logger="chap_coordinator"):
        ok(send("participant.join", "human:a", type="human"))
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "workspace w" in errors[0].getMessage()
    assert "disk full" in errors[0].getMessage()


# ---- SqliteStore ----------------------------------------------------


def test_sqlite_holds_each_entry_once_and_restarts_to_the_same_state(tmp_path):
    path = str(tmp_path / "rt.db")
    store = SqliteStore(path)
    c = coordinator(store)
    exercise(c)
    (count,) = store._conn.execute("SELECT COUNT(*) FROM chap_entries").fetchone()
    assert count == len(c.workspaces["w"].audit)
    store.close()
    reopened = SqliteStore(path)
    assert plain(coordinator(reopened)) == plain(c)
    reopened.close()


def test_a_view_in_the_old_tables_place_reads_the_current_state(tmp_path):
    store = SqliteStore(str(tmp_path / "view.db"))
    c = coordinator(store)
    exercise(c)
    ws_id, data, version = store._conn.execute(
        "SELECT id, data, version FROM chap_workspaces").fetchone()
    data = json.loads(data)
    ws = c.workspaces["w"]
    assert ws_id == "w" and version == len(ws.audit)
    assert [e["seq"] for e in data["audit"]] == [e.seq for e in ws.audit]
    assert sorted(data["tasks"]) == sorted(ws.tasks)
    assert data["chain_head"] == ws.chain_head
    store.close()


def test_a_file_from_an_earlier_release_is_moved_into_the_new_tables(tmp_path, caplog):
    source = coordinator()
    exercise(source)
    snapshot = source._snapshot_workspace(source.workspaces["w"]).data
    # The TypeScript coordinator wrote its collections as lists.
    listed = {**snapshot, "id": "w-ts"}
    for name in COLLECTIONS:
        listed[name] = list(snapshot[name].values())
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE chap_workspaces (id TEXT PRIMARY KEY, data TEXT NOT NULL, "
        "version INTEGER NOT NULL, updated_at TEXT NOT NULL);"
        "CREATE INDEX idx_chap_workspaces_updated_at ON chap_workspaces (updated_at);")
    for row in (("w", json.dumps(snapshot, sort_keys=True)), ("w-ts", json.dumps(listed)),
                ("w-bad", "{bad json")):
        old.execute("INSERT INTO chap_workspaces VALUES (?, ?, 7, 't1')", row)
    old.commit()
    old.close()

    with caplog.at_level(logging.WARNING):
        store = SqliteStore(path)
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "w-bad" in warned[0] and "chap_workspaces_v1" in warned[0]
    c = coordinator(store)
    assert sorted(c.workspaces) == ["w", "w-ts"]
    assert plain(c)["w"] == plain(source)["w"]
    assert len(c.workspaces["w-ts"].audit) == len(snapshot["audit"])
    kept = store._conn.execute(
        "SELECT id, data FROM chap_workspaces_v1 ORDER BY id").fetchall()
    assert [r[0] for r in kept] == ["w", "w-bad", "w-ts"]
    assert kept[1][1] == "{bad json"
    assert store._conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'idx_chap_workspaces_updated_at'").fetchone() is None
    ok(driver(c)("participant.join", "human:e", type="human"))
    store.close()
    again = SqliteStore(path)
    assert plain(coordinator(again)) == plain(c)
    again.close()


def test_a_failed_commit_leaves_no_transaction_open(tmp_path):
    """A COMMIT that fails, on a busy file say, must not wedge the store."""
    path = str(tmp_path / "commit.db")
    store = SqliteStore(path)

    class FailsOnce:
        def __init__(self, conn):
            self.conn, self.armed = conn, True

        def execute(self, sql, *args):
            if sql == "COMMIT" and self.armed:
                self.armed = False
                raise sqlite3.OperationalError("database is locked")
            return self.conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.conn, name)

    seen = []
    c = coordinator(store, on_store_error=lambda e, r: seen.append(e))
    real = store._conn
    store._conn = FailsOnce(real)
    send = driver(c)
    ok(send("workspace.create", "human:a"))
    assert len(seen) == 1 and not real.in_transaction
    ok(send("participant.join", "human:a", type="human"))
    assert len(seen) == 1
    store._conn = real
    store.close()
    reopened = SqliteStore(path)
    assert plain(coordinator(reopened)) == plain(c)
    reopened.close()


def test_an_applications_own_view_of_the_old_table_reads_the_current_state(tmp_path):
    path = str(tmp_path / "userview.db")
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE chap_workspaces (id TEXT PRIMARY KEY, data TEXT NOT NULL, "
        "version INTEGER NOT NULL, updated_at TEXT NOT NULL);"
        "CREATE VIEW my_report AS SELECT id, json_array_length(data, '$.audit') AS n "
        "FROM chap_workspaces;")
    old.execute("INSERT INTO chap_workspaces VALUES ('w', ?, 1, 't')", (json.dumps(
        {"id": "w", "created": "t", "state": "active", "profiles": ["core/1.0"],
         "members": {}, "tasks": {}, "audit": []}),))
    old.commit()
    old.close()
    store = SqliteStore(path)
    ok(driver(Coordinator(CoordinatorOptions(store=store)))(
        "participant.join", "agent:a", type="agent"))
    assert tuple(store._conn.execute("SELECT id, n FROM my_report").fetchone()) == ("w", 1)
    store.close()


def _open_once(path, barrier, results):
    barrier.wait()
    try:
        SqliteStore(path).close()
        results.put("ok")
    except Exception as exc:  # pragma: no cover - the failure being guarded
        results.put(f"{type(exc).__name__}: {exc}")


def test_processes_opening_one_new_file_at_once_all_succeed(tmp_path):
    import multiprocessing as mp
    ctx = mp.get_context("spawn")
    for trial in range(3):
        path = str(tmp_path / f"fresh{trial}.db")
        barrier, results = ctx.Barrier(4), ctx.Queue()
        procs = [ctx.Process(target=_open_once, args=(path, barrier, results)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
        outcomes = [results.get(timeout=5) for _ in procs]
        assert outcomes == ["ok"] * 4, outcomes


def test_two_coordinators_on_one_file_the_second_writer_is_refused(tmp_path):
    path = str(tmp_path / "two.db")
    errors = []

    def make():
        return coordinator(SqliteStore(path), on_store_error=lambda e, r: errors.append(e))
    a = make()
    ok(driver(a)("workspace.create", "human:alice"))
    b = make()
    ok(driver(b)("participant.join", "human:bob", type="human"))
    ok(driver(a)("participant.join", "human:carol", type="human"))
    assert len(errors) == 1 and isinstance(errors[0], StoreConflictError)
    survivor = make().workspaces["w"]
    assert "human:bob" in survivor.members
    assert "human:carol" not in survivor.members
