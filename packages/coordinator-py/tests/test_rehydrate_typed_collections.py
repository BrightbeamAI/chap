"""
Regression (#153): every typed collection on Workspace comes back typed.

``_snapshot_workspace`` persists through ``asdict``, so every collection is
serialised. ``_rehydrate_workspace`` rebuilt six of them and let ``overrides``
and ``route_decisions`` through as raw dicts, which diverges from the
TypeScript ``restore`` and breaks the first attribute read after a restart.
"""
from __future__ import annotations

from dataclasses import asdict

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.coordinator import _rehydrate_workspace
from chap_coordinator.storage.store import MemoryStore
from chap_coordinator.types import (
    Deliberation, Handoff, Member, OverrideArtefact, RouteDecisionArtefact,
    SnapshotArtefact, Task, WhisperPrompt,
)

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "routing/1.0",
            "whisper/1.0", "deliberation/1.0", "handoff/1.0"]

TYPED = {
    "members": Member, "tasks": Task, "overrides": OverrideArtefact,
    "whispers": WhisperPrompt, "deliberations": Deliberation,
    "handoffs": Handoff, "snapshots": SnapshotArtefact,
    "route_decisions": RouteDecisionArtefact,
}


def _send(coord, method, **params):
    return coord.dispatch({
        "jsonrpc": "2.0", "id": method, "method": method, "params": params,
    })


def _busy_workspace(coord):
    """A workspace with something in every typed collection."""
    _send(coord, "workspace.create", workspace="w", profiles=PROFILES)
    for uri, kind in (("human:a", "human"), ("agent:b", "agent")):
        _send(coord, "participant.join", workspace="w", **{"from": uri}, type=kind)
    made = _send(coord, "task.create", workspace="w", **{"from": "human:a"},
                 kind="draft", input={"text": "before"}, assignee="agent:b",
                 review_required=True)
    task_id = made["result"]["task_id"]
    _send(coord, "task.complete", workspace="w", **{"from": "agent:b"},
          task_id=task_id, output={"text": "before"})
    _send(coord, "decide.override", workspace="w", **{"from": "human:a"},
          task_id=task_id, rationale="tone",
          diff=[{"op": "replace", "path": "/text", "value": "after"}])
    _send(coord, "task.route", workspace="w", **{"from": "human:a"},
          task_id=task_id, candidates=["agent:b", "human:a"])
    _send(coord, "control.snapshot", workspace="w", **{"from": "human:a"},
          include=["mode_ceiling"])
    _send(coord, "whisper.ask", workspace="w", **{"from": "agent:b"},
          task_id=task_id, to=["human:a"], question="ship it?",
          deadline_ms=60_000, default_if_lapsed="hold")
    _send(coord, "deliberate.open", workspace="w", **{"from": "human:a"},
          task_id=task_id, question="ship it?", rule="quorum:2",
          participants=["human:a", "agent:b"])
    _send(coord, "handoff.propose", workspace="w", **{"from": "agent:b"},
          to="human:a", tasks=[{"task_id": task_id}])
    return task_id


def test_every_typed_collection_is_rebuilt_on_rehydrate():
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=PROFILES))
    _busy_workspace(coord)
    live = coord.get_workspace("w")
    for name, cls in TYPED.items():
        assert getattr(live, name), f"{name} is empty, so the test proves nothing"

    restored = _rehydrate_workspace(asdict(live))
    for name, cls in TYPED.items():
        for key, value in getattr(restored, name).items():
            assert isinstance(value, cls), f"{name}[{key}] came back as {type(value).__name__}"


def test_an_override_survives_a_restart_as_an_artefact():
    store = MemoryStore()
    options = CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, store=store,
        default_profiles=PROFILES)
    task_id = _busy_workspace(Coordinator(options))

    restarted = Coordinator(options).get_workspace("w")
    override = next(o for o in restarted.overrides.values() if o.task_id == task_id)
    assert override.result == {"text": "after"}
    assert override.rationale == "tone"
    assert override.to_dict()["kind"] == "override"
    decision = next(iter(restarted.route_decisions.values()))
    assert decision.decision_type == "task.route"
    assert decision.to_dict()["produced_by"] == "service:coordinator"
