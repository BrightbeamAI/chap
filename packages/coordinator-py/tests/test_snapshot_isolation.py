"""A captured control snapshot must not alias live or returned state."""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def _send(coord, method, **params):
    return coord.dispatch({
        "jsonrpc": "2.0", "id": method, "method": method, "params": params,
    })


def _ready():
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True))
    _send(coord, "workspace.create", workspace="w")
    _send(coord, "participant.join", workspace="w", **{"from": "human:a"},
          type="human", scopes=["review"])
    _send(coord, "participant.join", workspace="w", **{"from": "agent:b"},
          type="agent")
    task_id = _send(
        coord, "task.create", workspace="w", **{"from": "human:a"},
        kind="draft", input={"nested": ["before"]}, assignee="agent:b",
    )["result"]["task_id"]
    _send(
        coord, "review.request", workspace="w", **{"from": "agent:b"},
        task_id=task_id, to=["human:a"], artefact={"text": "draft"},
    )
    return coord, task_id


def test_snapshot_isolated_from_live_mutations_and_response_mutations():
    coord, task_id = _ready()
    result = _send(
        coord, "control.snapshot", workspace="w", **{"from": "human:a"},
        include=["members", "open_tasks"],
    )["result"]
    snapshot_id = result["snapshot_artefact_id"]
    workspace = coord.get_workspace("w")
    assert workspace is not None
    saved = workspace.snapshots[snapshot_id]

    # Mutating the response returned to a library caller must not mutate the
    # artefact retained for rollback.
    returned = result["artefact"]
    returned["content"]["state"]["open_tasks"][0]["input"]["nested"].append("response")
    assert saved.state["open_tasks"][0]["input"]["nested"] == ["before"]

    # Mutating live nested values after capture must not rewrite the captured
    # projection either.
    workspace.members["human:a"].scopes.append("live")
    task = workspace.tasks[task_id]
    task.input["nested"].append("live")
    task.review.decisions.append({"reviewer": "human:a", "kind": "approve"})
    assert saved.state["members"][0]["scopes"] == ["review"]
    captured_task = next(t for t in saved.state["open_tasks"] if t["id"] == task_id)
    assert captured_task["input"]["nested"] == ["before"]
    assert captured_task["review"]["decisions"] == []
