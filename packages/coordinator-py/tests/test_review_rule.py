"""
The workspace's review rule (SPECIFICATION 6.1.1, review profile 2.1).

An author may not open a review of their own review-required task, so a
review opens on task.complete. A workspace created with review_rule opens
every such review under that rule: an agent cannot ask for fewer approvals
than the workspace sets. Mirrors review_rule.test.ts.
"""
from __future__ import annotations

import dataclasses

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.storage.store import MemoryStore


def ready(coord=None):
    c = coord or Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))
    n = [0]

    def call(method, frm, **params):
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
                           "params": {"workspace": "w", "from": frm, **params}})
    return call


def reviewed(call):
    for h in ("human:a@x", "human:b@x"):
        call("participant.join", h, type="human")
    call("participant.join", "agent:x", type="agent")
    task_id = call("task.create", "agent:x", kind="k", assignee="agent:x", input={},
                   review_required=True)["result"]["task_id"]
    call("task.complete", "agent:x", task_id=task_id, output={"a": 1})
    return task_id


def test_a_review_opened_on_task_complete_takes_the_workspace_rule():
    call = ready()
    assert "result" in call("workspace.create", "human:a@x", review_rule="quorum:2")
    assert call("workspace.describe", "human:a@x")["result"]["review_rule"] == "quorum:2"
    task_id = reviewed(call)
    assert call("decide.approve", "human:a@x", task_id=task_id)["result"]["state"] == "review_requested"
    assert call("decide.approve", "human:b@x", task_id=task_id)["result"]["state"] == "completed"


def test_without_a_rule_the_workspace_opens_reviews_under_any_one_approves():
    call = ready()
    call("workspace.create", "human:a@x")
    assert call("workspace.describe", "human:a@x")["result"]["review_rule"] == "any_one_approves"
    task_id = reviewed(call)
    assert call("decide.approve", "human:a@x", task_id=task_id)["result"]["state"] == "completed"


@pytest.mark.parametrize("rule", ["quorum:0", "quorum:x", "most", 2])
def test_an_unknown_rule_is_refused_at_workspace_create(rule):
    assert ready()("workspace.create", "human:a@x", review_rule=rule)["error"]["code"] == -32602


def test_the_rule_survives_a_restart():
    store = MemoryStore()
    ready(Coordinator(CoordinatorOptions(store=store, deterministic_ids=True,
                                         deterministic_clock=True)))(
        "workspace.create", "human:a@x", review_rule="all_approve")
    again = ready(Coordinator(CoordinatorOptions(store=store)))
    assert again("workspace.describe", "human:a@x")["result"]["review_rule"] == "all_approve"


def test_a_stored_rule_this_release_does_not_support_is_read_as_none():
    store = MemoryStore()
    ready(Coordinator(CoordinatorOptions(store=store, deterministic_ids=True,
                                         deterministic_clock=True)))(
        "workspace.create", "human:a@x", review_rule="quorum:2")
    record = store.load_one("w")
    store.save(dataclasses.replace(record, data={**record.data, "review_rule": "quorum:٢"}))
    again = ready(Coordinator(CoordinatorOptions(store=store)))
    assert again("workspace.describe", "human:a@x")["result"]["review_rule"] == "any_one_approves"
