"""
Signature checks follow the workspace, and the "humans" policy.

Signatures were checked only where the coordinator option was on. An admin's
workspace.set_profiles could add security-signed/1.0 and unsigned calls were
still accepted, and a store written under the option and reopened without it
advertised the profile and accepted unsigned calls (29 October and 6 November
reviews). A workspace now requires signatures when it advertises the profile
or the option is on, cannot stop advertising it, and under the "humans" policy
takes unsigned calls from its agents.

Mirrors packages/coordinator/tests/signature_policy.test.ts.
"""
from __future__ import annotations

import json

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import derive_private_key, public_jwk, sign
from chap_coordinator.storage.store import MemoryStore

PROFILES = ["core/1.0", "review/1.0"]
SIGNED = [*PROFILES, "security-signed/1.0"]
TASK = {"kind": "k", "input": {}, "assignee": "agent:b"}


class _Ready:
    def __init__(self, create=None, **options):
        self.c = Coordinator(CoordinatorOptions(deterministic_ids=True, **options))
        self._n = 0
        self.unsigned("workspace.create", "human:a", **(create or {"profiles": PROFILES}))
        for uri, kind, role in (("human:a", "human", "admin"), ("human:c", "human", "reviewer"),
                                ("agent:b", "agent", "drafter")):
            self.unsigned("participant.join", uri, type=kind, role=role,
                          jwks={"keys": [public_jwk(uri, derive_private_key(uri))]})

    def _call(self, method, frm, params):
        self._n += 1
        return {"jsonrpc": "2.0", "id": f"c{self._n}", "method": method,
                "params": {"workspace": "w", "from": frm, **params}}

    def unsigned(self, method, frm, **params):
        return self.c.dispatch(self._call(method, frm, params))

    def signed(self, method, frm, **params):
        env = self._call(method, frm, params)
        key = derive_private_key(frm)
        env["sig"] = sign(canonicalize(env), key, public_jwk(frm, key)["kid"])
        return self.c.dispatch(env)

    @property
    def ws(self):
        return self.c.workspaces["w"]


def test_adding_security_signed_with_set_profiles_turns_the_checks_on():
    r = _Ready()
    assert "result" in r.unsigned("workspace.set_profiles", "human:a", profiles=SIGNED)
    assert r.unsigned("task.create", "agent:b", **TASK)["error"]["code"] == -32070


def test_a_store_reopened_without_the_option_still_requires_signatures():
    store = MemoryStore()
    _Ready(store=store, require_signatures=True)
    again = Coordinator(CoordinatorOptions(store=store))
    r = again.dispatch({"jsonrpc": "2.0", "id": "x", "method": "task.create",
                        "params": {"workspace": "w", "from": "agent:b", **TASK}})
    assert r["error"]["code"] == -32070


def test_a_workspace_that_advertises_security_signed_cannot_stop_advertising_it():
    r = _Ready(create={"profiles": SIGNED})
    refused = r.signed("workspace.set_profiles", "human:a", profiles=PROFILES)
    assert refused["error"] == {"code": -32011,
                                "message": "security-signed/1.0 cannot be removed once advertised"}
    assert r.ws.profiles == SIGNED
    # A later version of the profile keeps it advertised.
    assert "result" in r.signed("workspace.set_profiles", "human:a",
                                profiles=[*PROFILES, "security-signed/1.1"])


def test_under_the_humans_policy_an_agents_unsigned_call_is_taken_and_a_persons_is_not():
    r = _Ready(create={"profiles": SIGNED, "signature_policy": "humans"})
    created = r.unsigned("task.create", "agent:b", **TASK, review_required=True)
    assert "error" not in created
    tid = created["result"]["task_id"]
    assert r.unsigned("task.complete", "agent:b", task_id=tid,
                      output={"v": 1})["result"]["state"] == "review_requested"
    assert r.unsigned("decide.approve", "human:a", task_id=tid)["error"]["code"] == -32070
    # A call that is signed is checked, from an agent as from anyone.
    forged = r.c.dispatch({"jsonrpc": "2.0", "id": "f", "method": "task.update",
                           "sig": "ed25519:x:AAAA",
                           "params": {"workspace": "w", "from": "agent:b", "task_id": tid,
                                      "state": "in_progress"}})
    assert forged["error"]["code"] == -32071
    # A sender who is not a member is not taken unsigned.
    assert r.unsigned("task.create", "agent:ghost", **TASK)["error"]["code"] == -32070
    assert r.signed("decide.approve", "human:a", task_id=tid)["result"]["state"] == "completed"


def test_the_policy_comes_from_workspace_create_else_the_option_and_describe_returns_it():
    named = _Ready(create={"profiles": SIGNED, "signature_policy": "humans"})
    assert named.signed("workspace.describe", "human:a")["result"]["signature_policy"] == "humans"
    fallback = _Ready(create={"profiles": SIGNED}, signature_policy="humans")
    assert fallback.ws.signature_policy == "humans"
    plain = _Ready()
    assert plain.unsigned("workspace.describe", "human:a")["result"]["signature_policy"] == "all"
    c = Coordinator()
    r = c.dispatch({"jsonrpc": "2.0", "id": "x", "method": "workspace.create",
                    "params": {"workspace": "w", "signature_policy": "agents"}})
    assert r["error"] == {"code": -32602, "message": "signature_policy must be all or humans"}
    # The params are checked before the name is, as they are in TypeScript.
    taken = plain.c.dispatch({"jsonrpc": "2.0", "id": "y", "method": "workspace.create",
                              "params": {"workspace": "w", "signature_policy": 5}})
    assert taken["error"] == {"code": -32602, "message": "signature_policy must be all or humans"}


def test_the_policy_is_kept_by_the_store_and_a_workspace_stored_without_one_verifies_every_call():
    store = MemoryStore()
    _Ready(create={"profiles": SIGNED, "signature_policy": "humans"}, store=store)
    assert Coordinator(CoordinatorOptions(store=store)).workspaces["w"].signature_policy == "humans"
    held = store._held["w"]
    meta = json.loads(held["meta"])
    del meta["signature_policy"]
    held["meta"] = json.dumps(meta)
    assert Coordinator(CoordinatorOptions(store=store)).workspaces["w"].signature_policy == "all"
