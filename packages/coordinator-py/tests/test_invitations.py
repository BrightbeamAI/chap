"""
workspace.invite and the join policy (SPECIFICATION 6.3.3).

workspace.invite was specified and not built. Nobody could be admitted by an
admin, a workspace could not limit who joins, and a member whose keys were
all revoked, or who held none where signatures are required, could never sign
again. An admin now invites: the invitation names the role the member takes
and may pin its keys and its OIDC subject; a workspace whose join_policy is
"invite" admits only invited participants and its first member, its admin;
and an invitation for a member that holds no key in force registers new ones.
Mirrors invitations.test.ts.
"""
from __future__ import annotations

import json

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import derive_private_key, public_jwk, sign
from chap_coordinator.storage.sqlite import SqliteStore
from chap_coordinator.storage.store import MemoryStore, WorkspaceRecord


def key_of(seed):
    return public_jwk(seed, derive_private_key(seed))


class _Ready:
    def __init__(self, **options):
        self.c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True,
                                                **options))
        self._n = 0

    def _env(self, method, frm, params):
        self._n += 1
        return {"jsonrpc": "2.0", "id": f"c{self._n}", "method": method,
                "params": {"workspace": "w", "from": frm, **params}}

    def call(self, method, frm, **params):
        return self.c.dispatch(self._env(method, frm, params))

    def signed(self, method, frm, key_seed=None, **params):
        """A call signed with the key derive_private_key gives ``key_seed``,
        by default the sender's."""
        env = self._env(method, frm, params)
        seed = key_seed or frm
        key = derive_private_key(seed)
        env["sig"] = sign(canonicalize(env), key, public_jwk(seed, key)["kid"])
        return self.c.dispatch(env)

    @property
    def ws(self):
        return self.c.workspaces["w"]


def test_a_workspace_that_admits_by_invitation_takes_its_first_member_as_its_admin():
    r = _Ready()
    r.call("workspace.create", "human:a", join_policy="invite")
    assert r.call("workspace.describe", "human:a")["result"]["join_policy"] == "invite"
    assert r.call("participant.join", "agent:bot", type="agent")["error"] == {
        "code": -32011, "message": "agent:bot has no invitation to this workspace"}
    assert r.call("participant.join", "human:a", type="human", role="reviewer")["error"] == {
        "code": -32602,
        "message": "The first member of a workspace that admits by invitation is its admin"}
    assert r.call("participant.join", "human:a", type="human")["result"] == {
        "joined": True, "as": "human:a"}
    assert r.ws.members["human:a"].role == "admin"
    assert r.call("participant.join", "human:b", type="human")["error"]["code"] == -32011
    assert "human:b" not in r.ws.members


def test_an_invitation_admits_its_invitee_once_in_the_role_it_names():
    r = _Ready(join_policy="invite")
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin")
    assert r.call("workspace.invite", "human:a", uri="human:b", type="human",
                  role="reviewer")["result"] == {"invited": True, "uri": "human:b"}
    assert "error" not in r.call("participant.join", "human:b", type="human", role="drafter")
    assert r.ws.members["human:b"].role == "reviewer"
    assert len(r.ws.invitations) == 0
    r.call("participant.leave", "human:b")
    assert r.call("participant.join", "human:b", type="human")["error"]["code"] == -32011


def test_after_the_first_member_only_an_invitation_grants_the_admin_role():
    r = _Ready()
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin")
    r.call("workspace.invite", "human:a", uri="human:c", type="human", role="admin")
    r.call("workspace.invite", "human:a", uri="human:d", type="human", role="reviewer")
    assert "error" not in r.call("participant.join", "human:c", type="human", role="admin")
    assert r.ws.members["human:c"].role == "admin"
    assert r.call("participant.join", "human:d", type="human", role="admin")["error"] == {
        "code": -32011, "message": "The admin role is granted by an admin"}
    # An invitation that names no role leaves the join's own, admin excepted.
    r.call("workspace.invite", "human:c", uri="human:e", type="human")
    assert r.call("participant.join", "human:e", type="human", role="admin")["error"]["code"] == -32011
    assert "error" not in r.call("participant.join", "human:e", type="human", role="drafter")
    assert r.ws.members["human:e"].role == "drafter"


def test_only_an_admin_invites_and_an_invitations_names_are_checked_as_a_joins_are():
    r = _Ready()
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin")
    r.call("participant.join", "agent:b", type="agent")

    def invite(frm, **params):
        return r.call("workspace.invite", frm, **params).get("error")

    assert invite("agent:b", uri="human:x", type="human") == {
        "code": -32011, "message": "workspace.invite requires the admin role"}
    assert invite("human:ghost", uri="human:x", type="human")["code"] == -32011
    assert invite("human:a", uri="human:x", type="agent") == {
        "code": -32602, "message": 'uri must start with "agent:" for type agent'}
    assert invite("human:a", uri="agent:x", type="agent", role="admin") == {
        "code": -32602, "message": "The admin role is held by a human or a service"}
    assert invite("human:a", uri="human:x", type="human", jwks={"keys": [{"kty": "OKP"}]}) == {
        "code": -32602, "message": "jwks names no key: each key needs a kid"}
    assert invite("human:a", uri="human:x", type="human", oidc_sub="s") == {
        "code": -32602,
        "message": "oidc_sub names a token subject, and this Coordinator has no token verifier"}
    assert invite("human:a", type="human")["message"] == "Missing field: uri"
    assert len(r.ws.invitations) == 0
    # A member's refused attempt is on the log; the admin's accepted one is too.
    before = len(r.ws.audit)
    invite("agent:b", uri="human:x", type="human")
    r.call("workspace.invite", "human:a", uri="human:x", type="human")
    assert [e.outcome["code"] if e.outcome else "accepted" for e in r.ws.audit[before:]] == [
        -32011, "accepted"]
    assert r.ws.invitations["human:x"].invited_by == "human:a"


def test_an_invitation_that_pins_keys_admits_only_those_keys_and_the_member_holds_them():
    r = _Ready()
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin")
    pinned = key_of("human:e")
    r.call("workspace.invite", "human:a", uri="human:e", type="human", jwks={"keys": [pinned]})
    other = key_of("human:e#mallory")
    assert r.call("participant.join", "human:e", type="human",
                  jwks={"keys": [other]})["error"] == {
        "code": -32011, "message": "human:e joins with keys its invitation does not name",
        "data": {"kids": [other["kid"]]}}
    # The pinned kid on other key material is another key.
    forged = {**other, "kid": pinned["kid"]}
    assert r.call("participant.join", "human:e", type="human",
                  jwks={"keys": [forged]})["error"]["code"] == -32011
    assert "human:e" not in r.ws.members
    assert "error" not in r.call("participant.join", "human:e", type="human")
    assert [k.jwk for k in r.ws.members["human:e"].keys] == [pinned]


def test_an_invitation_that_names_an_oidc_subject_admits_a_token_for_that_subject():
    r = _Ready(verify_oidc_token=lambda t: json.loads(t[4:]) if t.startswith("tok:") else None)
    r.call("workspace.create", "service:op")
    r.call("participant.join", "service:op", type="service", role="admin")
    r.call("workspace.invite", "service:op", uri="human:f", type="human", oidc_sub="sub-f")
    assert r.call("participant.join", "human:f", type="human")["error"]["code"] == -32403
    assert r.call("participant.join", "human:f", type="human",
                  oidc_token="tok:" + json.dumps({"sub": "sub-g"}))["error"] == {
        "code": -32404, "message": "OIDC token subject does not match the invitation"}
    assert "error" not in r.call("participant.join", "human:f", type="human",
                                 oidc_token="tok:" + json.dumps({"sub": "sub-f"}))
    assert r.ws.members["human:f"].oidc_sub == "sub-f"


def test_an_invitation_for_a_member_that_holds_no_key_in_force_registers_new_keys():
    # A signed workspace: the admin's calls are signed, and so are a person's.
    r = _Ready(require_signatures=True)
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin",
           jwks={"keys": [key_of("human:a")]})
    r.call("participant.join", "human:c", type="human", jwks={"keys": [key_of("human:c")]})
    fresh = key_of("human:c#2")
    invite = {"uri": "human:c", "type": "human", "jwks": {"keys": [fresh]}}
    assert r.signed("workspace.invite", "human:a", **invite)["error"] == {
        "code": -32602, "message": "human:c is a member and holds a key in force; its keys "
                                   "change through participant.rotate_key"}
    assert "error" not in r.signed("participant.revoke_key", "human:a", target_uri="human:c",
                                   kid=key_of("human:c")["kid"])
    assert r.signed("workspace.describe", "human:c")["error"]["code"] == -32072
    # The admin signs the invitation like any call in a signed workspace.
    assert r.call("workspace.invite", "human:a", **invite)["error"]["code"] == -32070
    assert r.signed("workspace.invite", "human:a", uri="human:c", type="human",
                    jwks={"keys": [key_of("human:c")]})["error"] == {
        "code": -32602,
        "message": f"Key {key_of('human:c')['kid']} was human:c's; a new key takes a new kid"}
    assert r.signed("workspace.invite", "human:a", **invite, role="admin")["error"]["code"] == -32602
    assert r.signed("workspace.invite", "human:a", **invite)["result"] == {
        "invited": True, "uri": "human:c", "registered": [fresh["kid"]]}
    assert "error" not in r.signed("workspace.describe", "human:c", key_seed="human:c#2")
    assert len(r.ws.invitations) == 0


def test_where_a_token_verifier_is_configured_an_invitation_does_not_register_a_persons_keys():
    # The admin could otherwise revoke a person's keys, invite them with a key
    # the admin holds, and sign as them. The person joins again with a token
    # that binds a new key.
    r = _Ready(verify_oidc_token=lambda t: json.loads(t[4:]) if t.startswith("tok:") else None)
    r.call("workspace.create", "human:a")

    def token(sub, uri):
        return "tok:" + json.dumps({"sub": sub, "chap_participant_uri": uri})
    assert "error" not in r.call("participant.join", "human:a", type="human", role="admin",
                                 oidc_token=token("sub-a", "human:a"))
    assert "error" not in r.call("participant.join", "human:c", type="human",
                                 oidc_token=token("sub-c", "human:c"))
    assert r.call("workspace.invite", "human:a", uri="human:c", type="human",
                  jwks={"keys": [key_of("human:a#2")]})["error"] == {
        "code": -32602,
        "message": "human:c registers a new key by joining again with a token that binds it; "
                   "an invitation does not register a person's keys here"}
    assert r.ws.members["human:c"].keys == []


def test_an_invitation_and_the_join_policy_are_kept_by_the_store():
    store = MemoryStore()
    first = _Ready(store=store, join_policy="invite")
    first.call("workspace.create", "human:a")
    first.call("participant.join", "human:a", type="human")
    first.call("workspace.invite", "human:a", uri="human:b", type="human", role="reviewer",
               jwks={"keys": [key_of("human:b")]})
    second = Coordinator(CoordinatorOptions(store=store, deterministic_ids=True))
    ws = second.workspaces["w"]
    assert ws.join_policy == "invite"
    assert ws.invitations["human:b"] == first.ws.invitations["human:b"]
    joined = second.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                              "params": {"workspace": "w", "from": "human:b", "type": "human"}})
    assert "error" not in joined
    third = Coordinator(CoordinatorOptions(store=store))
    assert len(third.workspaces["w"].invitations) == 0
    assert third.workspaces["w"].members["human:b"].role == "reviewer"


def test_a_workspace_written_before_the_join_policy_existed_admits_anyone():
    store = MemoryStore()
    _Ready(store=store).call("workspace.create", "human:a")
    record = store.load_one("w")
    data = dict(record.data)
    data.pop("join_policy", None)
    data.pop("invitations", None)
    old = MemoryStore()
    old.save(WorkspaceRecord(id="w", data=data, version=record.version,
                             updated_at=record.updated_at))
    c = Coordinator(CoordinatorOptions(store=old))
    assert c.workspaces["w"].join_policy == "open"
    assert len(c.workspaces["w"].invitations) == 0


def test_join_policy_is_open_or_invite():
    r = _Ready()
    assert r.call("workspace.create", "human:a", join_policy="closed")["error"] == {
        "code": -32602, "message": "join_policy must be open or invite"}


def test_the_sqlite_view_lists_invitations_and_a_view_written_before_them_is_replaced(tmp_path):
    path = str(tmp_path / "chap.db")
    store = SqliteStore(path)

    def view(conn):
        return conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'chap_workspaces'").fetchone()[0]

    current = view(store._conn)
    assert "'$.invitations'" in current
    store._conn.execute("DROP VIEW chap_workspaces")
    store._conn.execute(current.replace(
        ", '$.invitations', json((SELECT json_group_object(id, json(data)) "
        "FROM chap_objects WHERE workspace = m.workspace AND collection = 'invitations'))", ""))
    assert "invitations" not in view(store._conn)
    store.close()
    reopened = SqliteStore(path)
    assert view(reopened._conn) == current
    reopened.close()
