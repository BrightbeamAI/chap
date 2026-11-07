"""
Who signs, who joins, and which profiles stay (SPECIFICATION 5.2, 5.1, 6.3.3
and 6.5).

Under the "humans" policy a service called unsigned, though only an agent on
the server's own transport needs to. A person or service could join a
workspace that requires signatures with no key, and hold a name it could
never sign for. group: and workspace: URIs, which address everyone a review
or handoff covers, could join as members, and a workspace:<id> member then
stood in for the broadcast scope. An admin could drop identity-oidc/1.0 or
identity-vc/1.0 and switch off the check that people are who they say, and
any string passed as a profile id, security-signed/0.0-off included.
Mirrors join_rules.test.ts.
"""
from __future__ import annotations

import json

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import derive_private_key, public_jwk, sign

SIGNED = ["core/1.0", "review/1.0", "security-signed/1.0"]


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

    def signed(self, method, frm, **params):
        env = self._env(method, frm, params)
        key = derive_private_key(frm)
        env["sig"] = sign(canonicalize(env), key, public_jwk(frm, key)["kid"])
        return self.c.dispatch(env)

    @staticmethod
    def keys(uri):
        return {"jwks": {"keys": [key_of(uri)]}}


def test_under_the_humans_policy_only_an_agent_calls_unsigned():
    r = _Ready()
    r.call("workspace.create", "human:a", profiles=SIGNED, signature_policy="humans")
    r.call("participant.join", "human:a", type="human", role="admin", **r.keys("human:a"))
    r.call("participant.join", "service:ops", type="service", **r.keys("service:ops"))
    r.call("participant.join", "agent:b", type="agent")
    assert "error" not in r.call("workspace.describe", "agent:b")
    assert r.call("workspace.describe", "service:ops")["error"]["code"] == -32070
    assert "error" not in r.signed("workspace.describe", "service:ops")
    assert r.call("workspace.describe", "human:a")["error"]["code"] == -32070


def test_in_a_workspace_that_requires_signatures_a_participant_who_signs_joins_with_a_key():
    r = _Ready()
    r.call("workspace.create", "human:a", profiles=SIGNED, signature_policy="humans")
    assert r.call("participant.join", "human:a", type="human", role="admin")["error"] == {
        "code": -32602, "message": "human:a must sign in this workspace, and joins with no key"}
    assert "error" not in r.call("participant.join", "human:a", type="human", role="admin",
                                 **r.keys("human:a"))
    assert r.call("participant.join", "service:ops", type="service")["error"]["code"] == -32602
    # Under "humans" an agent calls unsigned, so it needs no key.
    assert "error" not in r.call("participant.join", "agent:b", type="agent")
    # Keys an invitation pins count.
    r.signed("workspace.invite", "human:a", uri="human:c", type="human", **r.keys("human:c"))
    assert "error" not in r.call("participant.join", "human:c", type="human")
    assert list(r.c.workspaces["w"].members) == ["human:a", "agent:b", "human:c"]


def test_under_the_all_policy_an_agent_joins_with_a_key_too():
    r = _Ready(require_signatures=True)
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin", **r.keys("human:a"))
    assert r.call("participant.join", "agent:b", type="agent")["error"]["code"] == -32602
    assert "error" not in r.call("participant.join", "agent:b", type="agent", **r.keys("agent:b"))


def test_a_key_a_token_binds_is_a_key_to_join_with():
    cnf = key_of("human:p")
    r = _Ready(require_signatures=True, verify_oidc_token=lambda t: (
        {"sub": "p", "chap_participant_uri": "human:p", "cnf": {"jwk": cnf}} if t == "p" else None))
    r.call("workspace.create", "human:p")
    assert "error" not in r.call("participant.join", "human:p", type="human", oidc_token="p")
    assert [k.kid for k in r.c.workspaces["w"].members["human:p"].keys] == [cnf["kid"]]


def test_group_and_workspace_uris_name_address_scopes_which_neither_join_nor_are_invited():
    r = _Ready()
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin")
    scopes = {"code": -32602,
              "message": "group: and workspace: URIs name address scopes, which cannot join"}
    assert r.call("participant.join", "workspace:w", type="workspace")["error"] == scopes
    assert r.call("participant.join", "group:ops", type="group")["error"] == scopes
    assert r.call("workspace.invite", "human:a", uri="group:ops", type="group")["error"] == scopes
    assert list(r.c.workspaces["w"].members) == ["human:a"]


def test_an_identity_profile_once_advertised_stays():
    r = _Ready(verify_oidc_token=lambda t: {"sub": t, "chap_participant_uri": f"human:{t}"},
               verify_vc=lambda vp: {"holder": vp.get("who")})
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin", oidc_token="a")

    def set_profiles(profiles):
        return r.call("workspace.set_profiles", "human:a", profiles=profiles)

    assert set_profiles(["core/1.0", "review/1.0", "identity-vc/1.0"])["error"] == {
        "code": -32011, "message": "identity-oidc/1.0 cannot be removed once advertised"}
    assert set_profiles(["core/1.0", "review/1.0", "identity-oidc/1.0"])["error"] == {
        "code": -32011, "message": "identity-vc/1.0 cannot be removed once advertised"}
    assert "error" not in set_profiles(["core/1.0", "review/1.0", "identity-oidc/1.0",
                                        "identity-vc/1.1"])


def test_a_profile_an_admin_adds_names_its_verifier():
    r = _Ready()
    r.call("workspace.create", "human:a")
    r.call("participant.join", "human:a", type="human", role="admin")
    assert r.call("workspace.set_profiles", "human:a",
                  profiles=["core/1.0", "identity-oidc/1.0"])["error"]["code"] == -32602
    assert r.call("workspace.set_profiles", "human:a",
                  profiles=["core/1.0", "identity-vc/1.0"])["error"]["code"] == -32602


def test_a_profile_id_names_a_catalogued_profile_at_its_major_version():
    r = _Ready()
    r.call("workspace.create", "human:a", profiles=["core/1.0", "review/1.0"])
    r.call("participant.join", "human:a", type="human", role="admin")

    # The id as JSON.stringify writes it, so the message reads as the
    # TypeScript reference's does.
    def unknown(profile_id):
        return {"code": -32602,
                "message": f"Unknown profile {json.dumps(profile_id, ensure_ascii=False)}: a profile "
                           "id is <name>/<major>.<minor> for a profile the catalogue lists"}

    for profile_id in ("security-signed/0.0-off", "security-signed/2.0", "security-signed",
                       "x-acme/1.0", "review/١.0", "review/01.0", "Review/1.0"):
        assert r.call("workspace.set_profiles", "human:a",
                      profiles=["core/1.0", profile_id])["error"] == unknown(profile_id)
        assert Coordinator(CoordinatorOptions()).dispatch(
            {"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
             "params": {"workspace": "v", "profiles": ["core/1.0", profile_id]}})["error"] \
            == unknown(profile_id)
    assert r.c.workspaces["w"].profiles == ["core/1.0", "review/1.0"]
    # A later minor version is the same profile (SPECIFICATION 15.4).
    assert "error" not in r.call("workspace.set_profiles", "human:a",
                                 profiles=["core/1.0", "review/1.1"])
    assert "error" not in Coordinator(CoordinatorOptions()).dispatch(
        {"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
         "params": {"workspace": "v", "profiles": ["core/1.0", "control/1.1"]}})
