"""Regression coverage for persisted control.snapshot artefacts."""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.store import MemoryStore
from chap_coordinator.types import SnapshotArtefact
from chap_coordinator.canonical import content_hash


def _send(coord, method, **params):
    return coord.dispatch({
        "jsonrpc": "2.0", "id": method, "method": method, "params": params,
    })


def test_snapshot_artefacts_rehydrate_as_dataclasses():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    _send(coord, "workspace.create", workspace="w", profiles=["core/1.0", "review/1.0", "control/1.0"])
    workspace = coord.get_workspace("w")
    assert workspace is not None
    content = {"workspace": "w", "audit_seq": 3, "include": ["mode_ceiling"],
               "state": {"mode_ceiling": "trial"}}
    workspace.snapshots["art_test"] = SnapshotArtefact(
        id="art_test", produced_at="2026-01-01T00:00:00.000Z", produced_by="human:a",
        content=content, content_hash=content_hash(content),
    )

    from dataclasses import asdict
    from chap_coordinator.coordinator import _rehydrate_workspace

    restored = _rehydrate_workspace(asdict(workspace))
    assert isinstance(restored.snapshots["art_test"], SnapshotArtefact)
    assert restored.snapshots["art_test"].content["state"] == {"mode_ceiling": "trial"}


def test_snapshot_rollback_works_after_store_restart():
    store = MemoryStore()
    options = CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, store=store,
    )
    first = Coordinator(options)
    _send(first, "workspace.create", workspace="w",
          profiles=["core/1.0", "review/1.0", "control/1.0"])
    _send(first, "participant.join", workspace="w", **{"from": "human:a"}, type="human")
    snap = _send(
        first, "control.snapshot", workspace="w", **{"from": "human:a"},
        include=["mode_ceiling"],
    )["result"]["snapshot_artefact_id"]
    _send(
        first, "control.set_mode_ceiling", workspace="w", **{"from": "human:a"},
        new_ceiling="shadow",
    )

    restarted = Coordinator(options)
    restored_snapshot = restarted.get_workspace("w").snapshots[snap]
    assert isinstance(restored_snapshot, SnapshotArtefact)
    result = _send(
        restarted, "control.rollback", workspace="w", **{"from": "human:a"},
        to_snapshot_artefact_id=snap, what_to_restore=["mode_ceiling"],
    )
    assert result["result"]["restored"] == ["mode_ceiling"]
    assert restarted.get_workspace("w").mode_ceiling == "production"
