"""
The admin role is granted, never claimed (SPECIFICATION 6.3).

Any joiner could ask for ``role: "admin"`` and get it, an agent included, and
with it drop the workspace's identity profile through workspace.set_profiles
or revoke a person's key through participant.revoke_key. The first
participant to join may take the role; nobody else can ask for it, and only a
person or a service holds it. Mirrors admin_role.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions

GRANTED = {"code": -32011, "message": "The admin role is granted by an admin"}


def _coordinator():
    c = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=["core/1.0", "review/1.0", "control/1.0"]))
    n = [0]

    def call(method, **params):
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"c{n[0]}", "method": method,
                           "params": {"workspace": "w", **params}})

    def join(frm, kind, **extra):
        return call("participant.join", **{"from": frm}, type=kind, **extra)

    def role_of(uri):
        m = c.workspaces["w"].members.get(uri)
        return m.role if m else None

    return c, call, join, role_of


def test_the_first_participant_to_join_may_take_the_admin_role_and_nobody_after():
    c, call, join, role_of = _coordinator()
    call("workspace.create")
    assert join("human:a", "human", role="admin")["result"] == {"joined": True, "as": "human:a"}
    assert role_of("human:a") == "admin"
    assert join("human:b", "human", role="admin")["error"] == GRANTED
    assert "human:b" not in c.workspaces["w"].members
    # Asking for another role is a join like any other.
    assert "error" not in join("human:b", "human", role="reviewer")
    assert role_of("human:b") == "reviewer"


def test_a_join_that_makes_its_workspace_is_the_first_member():
    _, _, join, role_of = _coordinator()
    assert "error" not in join("service:operator", "service", role="admin")
    assert role_of("service:operator") == "admin"


def test_only_a_person_or_a_service_holds_the_admin_role():
    _, call, join, _ = _coordinator()
    call("workspace.create")
    held = {"code": -32602, "message": "The admin role is held by a human or a service"}
    assert join("agent:bot", "agent", role="admin")["error"] == held
    assert join("group:ops", "group", role="admin")["error"] == held


def test_an_agent_that_asks_for_the_role_cannot_then_change_the_profiles_or_revoke_a_key():
    _, call, join, role_of = _coordinator()
    call("workspace.create")
    join("human:alice", "human",
         jwks={"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "k-alice", "x": "AAAA"}]})
    assert join("agent:bot", "agent", role="admin")["error"]["code"] == -32602
    join("agent:bot", "agent")
    assert role_of("agent:bot") == "participant"
    assert call("workspace.set_profiles", **{"from": "agent:bot"},
                profiles=["core/1.0"])["error"]["code"] == -32011
    assert call("participant.revoke_key", **{"from": "agent:bot"}, target_uri="human:alice",
                kid="k-alice")["error"]["code"] == -32011


def test_a_workspace_whose_members_have_all_left_has_had_its_first_member():
    _, call, join, _ = _coordinator()
    call("workspace.create")
    join("human:a", "human", role="admin")
    call("participant.leave", **{"from": "human:a"})
    assert join("human:m", "human", role="admin")["error"] == GRANTED


def test_a_member_joining_again_keeps_its_role_whatever_role_it_names():
    _, call, join, role_of = _coordinator()
    call("workspace.create")
    join("human:a", "human", role="admin")
    join("human:b", "human")
    assert join("human:b", "human", role="admin")["result"] == {"joined": True, "as": "human:b"}
    assert role_of("human:b") == "participant"


def test_a_rollback_neither_grants_the_admin_role_nor_takes_it_away():
    _, call, join, role_of = _coordinator()
    call("workspace.create")
    join("human:a", "human", role="admin")
    join("human:b", "human", role="reviewer")
    snap = call("control.snapshot", **{"from": "human:a"},
                include=["members"])["result"]["snapshot_artefact_id"]
    # The admin leaves, and someone else takes the name.
    call("participant.leave", **{"from": "human:a"})
    join("human:a", "human")
    call("participant.leave", **{"from": "human:b"})
    join("human:b", "human", role="drafter")
    r = call("control.rollback", **{"from": "human:b"}, to_snapshot_artefact_id=snap)
    assert r["result"]["restored"] == ["members"]
    assert role_of("human:a") == "participant"
    # Roles other than admin are restored as before.
    assert role_of("human:b") == "reviewer"
    assert call("workspace.set_profiles", **{"from": "human:a"},
                profiles=["core/1.0"])["error"]["code"] == -32011
