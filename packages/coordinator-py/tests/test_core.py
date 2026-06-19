"""Tests for CHAP Core methods."""
from __future__ import annotations

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
    ))


def send(coord, method, **params):
    env = {"jsonrpc": "2.0", "id": f"t-{method}", "method": method, "params": params}
    return coord.dispatch(env)


def test_workspace_create(coord):
    r = send(coord, "workspace.create", workspace="wsp_a")
    assert "result" in r
    assert r["result"]["workspace"] == "wsp_a"


def test_workspace_describe_unknown(coord):
    r = send(coord, "workspace.describe", workspace="wsp_missing")
    assert "error" in r


def test_participant_join_auto_creates_workspace(coord):
    r = send(coord, "participant.join",
             workspace="wsp_auto",
             **{"from": "human:alice@x", "type": "human", "role": "reviewer"})
    assert "result" in r and r["result"]["joined"] is True


def test_task_create_requires_assignee_in_workspace(coord):
    send(coord, "workspace.create", workspace="wsp_t")
    r = send(coord, "task.create", workspace="wsp_t",
             **{"from": "human:alice@x", "kind": "k", "input": {}},
             assignee="agent:not-joined")
    assert "error" in r


def test_task_lifecycle(coord):
    send(coord, "workspace.create", workspace="wsp_t")
    send(coord, "participant.join", workspace="wsp_t",
         **{"from": "human:alice@x", "type": "human", "role": "owner"})
    send(coord, "participant.join", workspace="wsp_t",
         **{"from": "agent:bot", "type": "agent", "role": "drafter"})
    r = send(coord, "task.create", workspace="wsp_t",
             **{"from": "human:alice@x", "kind": "draft", "input": {"q": "?"}},
             assignee="agent:bot")
    assert "result" in r
    tid = r["result"]["task_id"]

    # Transition created -> in_progress
    r = send(coord, "task.update", workspace="wsp_t", task_id=tid,
             state="in_progress", **{"from": "agent:bot"})
    assert r["result"]["state"] == "in_progress"

    # Complete
    r = send(coord, "task.complete", workspace="wsp_t", task_id=tid,
             output={"answer": "ok"}, **{"from": "agent:bot"})
    assert r["result"]["state"] == "completed"


def test_illegal_transition_rejected(coord):
    send(coord, "workspace.create", workspace="wsp_t")
    send(coord, "participant.join", workspace="wsp_t",
         **{"from": "human:alice@x", "type": "human", "role": "owner"})
    send(coord, "participant.join", workspace="wsp_t",
         **{"from": "agent:bot", "type": "agent", "role": "drafter"})
    r = send(coord, "task.create", workspace="wsp_t",
             **{"from": "human:alice@x", "kind": "k", "input": {}},
             assignee="agent:bot")
    tid = r["result"]["task_id"]
    # created -> completed is not legal directly
    r = send(coord, "task.update", workspace="wsp_t", task_id=tid,
             state="completed", **{"from": "agent:bot"})
    assert "error" in r


def test_audit_read_orders_by_seq(coord):
    send(coord, "workspace.create", workspace="wsp_a")
    send(coord, "participant.join", workspace="wsp_a",
         **{"from": "human:alice@x", "type": "human", "role": "r"})
    send(coord, "participant.join", workspace="wsp_a",
         **{"from": "agent:bot", "type": "agent", "role": "d"})
    r = send(coord, "audit.read", workspace="wsp_a")
    entries = r["result"]["entries"]
    assert [e["seq"] for e in entries] == list(range(len(entries)))


def test_unknown_method(coord):
    r = send(coord, "does.not.exist", workspace="x")
    assert r["error"]["code"] == -32601


def test_malformed_envelope(coord):
    r = coord.dispatch({"not_jsonrpc": True})
    assert "error" in r and r["error"]["code"] == -32600
