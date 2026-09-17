"""Python/TypeScript handoff parity for explicit empty acceptance lists."""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _send(coord, method, **params):
    return coord.dispatch({
        "jsonrpc": "2.0", "id": method, "method": method, "params": params,
    })


def test_explicit_empty_acceptance_is_not_implicit_accept_all():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    _send(coord, "workspace.create", workspace="w")
    _send(coord, "participant.join", workspace="w", **{"from": "human:alice"}, type="human")
    _send(coord, "participant.join", workspace="w", **{"from": "human:bob"}, type="human")
    task_id = _send(
        coord, "task.create", workspace="w", **{"from": "human:alice"},
        kind="handoff", input={}, assignee="human:alice",
    )["result"]["task_id"]
    handoff_id = _send(
        coord, "handoff.propose", workspace="w", **{"from": "human:alice", "to": "human:bob"},
        tasks=[{"task_id": task_id}],
    )["result"]["handoff_id"]

    result = _send(
        coord, "handoff.accept", workspace="w", **{"from": "human:bob"},
        handoff_id=handoff_id, accepted_task_ids=[],
    )
    assert result["result"]["task_ids"] == []
    assert coord.get_workspace("w").tasks[task_id].assignee == "human:alice"
