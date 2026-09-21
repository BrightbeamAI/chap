"""Refuse empty control selections before creating state or audit entries."""
from __future__ import annotations

import copy

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions

PROFILES = ["core/1.0", "review/1.0", "control/1.0"]


def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES,
                                       deterministic_ids=True, enable_chain=True))

    def send(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": "human:a", **params}})

    send("workspace.create", profiles=PROFILES)
    send("participant.join", type="human", scopes=["review"])
    return c, send


def test_snapshot_empty_include_is_refused_without_state_or_audit_mutation():
    c, send = _ready()
    before = copy.deepcopy(c.get_workspace("w"))
    reply = send("control.snapshot", include=[])
    assert reply["error"]["code"] == -32602
    assert "result" not in reply
    assert c.get_workspace("w") == before
    # Failed capture must not allocate an artefact id either.
    retry = send("control.snapshot", include=["mode_ceiling"])
    fresh, fresh_send = _ready()
    expected = fresh_send("control.snapshot", include=["mode_ceiling"])
    assert retry["result"]["snapshot_artefact_id"] == expected["result"]["snapshot_artefact_id"]


def test_snapshot_omitted_include_uses_defaults():
    c, send = _ready()
    content = send("control.snapshot")["result"]["artefact"]["content"]
    assert set(content["include"]) == {
        "members", "open_tasks", "mode_ceiling",
    }


def test_snapshot_nonempty_include_captures_only_selected_slice():
    c, send = _ready()
    content = send("control.snapshot", include=["mode_ceiling"])["result"]["artefact"]["content"]
    assert content["include"] == ["mode_ceiling"]
    assert content["state"] == {"mode_ceiling": c.get_workspace("w").mode_ceiling}


def test_rollback_empty_selection_is_refused_without_state_or_audit_mutation():
    c, send = _ready()
    sid = send("control.snapshot", include=["members", "mode_ceiling"])["result"]["snapshot_artefact_id"]
    ws = c.get_workspace("w")
    ws.mode_ceiling = "shadow"
    ws.members["human:a"].scopes = ["changed"]
    before = copy.deepcopy(c.get_workspace("w"))
    reply = send("control.rollback", to_snapshot_artefact_id=sid, what_to_restore=[])
    assert reply["error"]["code"] == -32602
    assert "result" not in reply
    assert c.get_workspace("w") == before


@pytest.mark.parametrize("partial", [False, True], ids=["omitted-restores-all", "nonempty-subset"])
def test_rollback_omitted_and_nonempty_selections_keep_existing_semantics(partial):
    c, send = _ready()
    ws = c.get_workspace("w")
    original_ceiling = ws.mode_ceiling
    sid = send("control.snapshot", include=["members", "mode_ceiling"])["result"]["snapshot_artefact_id"]
    ws.mode_ceiling = "shadow"
    ws.members["human:a"].scopes = ["changed"]
    params = {"what_to_restore": ["mode_ceiling"]} if partial else {}
    reply = send("control.rollback", to_snapshot_artefact_id=sid, **params)
    assert set(reply["result"]["restored"]) == ({"mode_ceiling"} if partial else {"members", "mode_ceiling"})
    assert ws.mode_ceiling == original_ceiling
    assert ws.members["human:a"].scopes == (["changed"] if partial else ["review"])
