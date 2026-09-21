"""Shared exact-response fixtures for the canonical snapshot wire artefact."""
from __future__ import annotations

import copy
import json
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import content_hash
from chap_coordinator.coordinator import _rehydrate_workspace
from chap_coordinator.storage.store import MemoryStore
from chap_coordinator.types import SnapshotArtefact

ROOT = Path(__file__).resolve().parents[3]
VECTORS = json.loads((ROOT / "conformance/control-snapshot-vectors.json").read_text())["vectors"]
SCHEMA = json.loads((ROOT / "schemas/core/chap-task.schema.json").read_text())["$defs"]["Artefact"]


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_exact_snapshot_response_and_chain(vector):
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True,
                                       enable_chain=True, default_profiles=vector["profiles"]))
    responses = [c.dispatch(copy.deepcopy(env)) for env in vector["envelopes"]]
    assert responses[-1] == vector["expected"]
    artefact = responses[-1]["result"]["artefact"]
    assert set(artefact) == set(SCHEMA["required"]) | {"content"}
    for key in ("id", "produced_by", "content_hash"):
        assert re.fullmatch(SCHEMA["properties"][key]["pattern"], artefact[key])
    assert artefact["content_hash"] == content_hash(artefact["content"])
    ws = c.get_workspace(vector["workspace"])
    stored = ws.snapshots[artefact["id"]]
    assert asdict(stored) == artefact
    assert stored.content is not artefact["content"]
    assert ws.chain_head == vector["expected_chain_head"]


def _ready(store=None):
    store = store if store is not None else MemoryStore()
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True, store=store))

    def send(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": "human:a", **params}})

    send("workspace.create", profiles=["core/1.0", "review/1.0", "control/1.0"])
    send("participant.join", type="human", scopes=["review"])
    return c, send, store


@pytest.mark.parametrize("terminal", ["completed", "declined", "cancelled", "superseded"])
def test_open_tasks_are_only_nonterminal_task_summaries(terminal):
    c, send, _ = _ready()
    def create():
        return send("task.create", kind="draft", input={"nested": ["not captured"]},
                    assignee="human:a")["result"]["task_id"]
    open_id, settled_id = create(), create()
    c.get_workspace("w").tasks[settled_id].state = terminal
    artefact = send("control.snapshot", include=["open_tasks"])["result"]["artefact"]
    assert artefact["content"]["state"]["open_tasks"] == [
        {"id": open_id, "kind": "draft", "state": "created", "assignee": "human:a"},
    ]


def test_hash_stays_stable_across_caller_mutation_rollback_and_store_restart():
    c, send, store = _ready()
    include = ["members", "mode_ceiling"]
    artefact = send("control.snapshot", include=include)["result"]["artefact"]
    expected = copy.deepcopy(artefact)
    saved = c.get_workspace("w").snapshots[artefact["id"]]
    include.append("audit")
    artefact["content"]["include"].append("policy")
    artefact["content"]["state"]["members"][0]["scopes"].append("caller")
    assert saved.to_dict() == expected
    c.get_workspace("w").members["human:a"].scopes.append("live")
    rollback = send("control.rollback", to_snapshot_artefact_id=saved.id)
    assert rollback["result"]["restored"] == ["mode_ceiling", "members"]
    c.get_workspace("w").members["human:a"].scopes.append("after-rollback")
    assert saved.to_dict() == expected
    assert saved.content_hash == content_hash(saved.content)
    send("control.set_mode_ceiling", new_ceiling="shadow")
    restarted = Coordinator(CoordinatorOptions(store=store))
    loaded = restarted.get_workspace("w").snapshots[saved.id]
    assert loaded.to_dict() == expected
    reply = restarted.dispatch({"jsonrpc": "2.0", "id": "rollback", "method": "control.rollback",
                                "params": {"workspace": "w", "from": "human:a",
                                           "to_snapshot_artefact_id": saved.id}})
    assert reply["result"]["restored"] == ["mode_ceiling", "members"]
    assert restarted.get_workspace("w").mode_ceiling == "production"
    assert restarted.get_workspace("w").members["human:a"].scopes == ["review"]
    assert loaded.to_dict() == expected


def test_legacy_flat_store_record_normalizes_to_one_canonical_representation():
    c, send, _ = _ready()
    artefact = send("control.snapshot", include=["mode_ceiling"])["result"]["artefact"]
    record = asdict(c.get_workspace("w"))
    record["snapshots"][artefact["id"]] = {
        "id": artefact["id"], "ts": artefact["produced_at"], "by": artefact["produced_by"],
        **copy.deepcopy(artefact["content"]),
    }
    restored = _rehydrate_workspace(record)
    assert restored.snapshots[artefact["id"]].to_dict() == artefact


def test_zero_audit_sequence_is_not_omitted_as_empty():
    content = {"workspace": "w", "audit_seq": 0, "include": ["audit"], "state": {"audit_seq": 0}}
    snap = SnapshotArtefact(id="art_test", produced_by="human:a", produced_at="2026-01-01T00:00:00Z",
                            content=content, content_hash=content_hash(content))
    assert snap.to_dict()["content"]["state"] == {"audit_seq": 0}


def test_legacy_full_bodies_and_empty_fields_are_projected_before_hashing():
    c, send, _ = _ready()
    send("participant.join", **{"from": "agent:b"}, type="agent")
    send("task.create", kind="draft", input={}, assignee="agent:b")
    artefact = send("control.snapshot")["result"]["artefact"]
    record = asdict(c.get_workspace("w"))
    content = copy.deepcopy(artefact["content"])
    content["state"]["members"][1]["scopes"] = []
    content["state"]["members"][1]["capabilities"] = {"excluded": True}
    content["state"]["open_tasks"][0]["input"] = {"excludedDecimal": 0.5}
    record["snapshots"][artefact["id"]] = {
        "id": artefact["id"], "ts": artefact["produced_at"], "by": artefact["produced_by"],
        **content,
    }
    restored = _rehydrate_workspace(record)
    assert restored.snapshots[artefact["id"]].to_dict() == artefact


@pytest.mark.parametrize("reason", [None, "", "restore checkpoint"])
def test_rollback_response_omits_absent_or_empty_reason(reason):
    _, send, _ = _ready()
    snapshot = send("control.snapshot", include=["mode_ceiling"])["result"]
    params = {} if reason is None else {"reason": reason}
    reply = send("control.rollback", to_snapshot_artefact_id=snapshot["snapshot_artefact_id"], **params)
    assert reply["result"] == {
        "rolled_back_to": snapshot["snapshot_artefact_id"], "audit_seq": snapshot["audit_seq"],
        "restored": ["mode_ceiling"], **({"reason": reason} if reason else {}),
    }
