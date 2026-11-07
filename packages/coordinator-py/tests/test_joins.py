"""
participant.join: types and URIs, identity required of people, credentials
kept off the chain, and a key bound through the token's nonce
(6 November review, items 4 and 5).

A join took any type with any URI, so "human:mallory" could join as an agent.
Where a token verifier was configured a person could join with no token at
all, and a join's token or presentation was written to the log, where
audit.read handed it to any reader. Mirrors joins.test.ts.
"""
from __future__ import annotations

import hashlib
import json

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import jwk_thumbprint
from chap_coordinator.storage.store import MemoryStore

RFC8037_KEY = {"kty": "OKP", "crv": "Ed25519", "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}


def _key(kid, x=None):
    return {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": x or kid.upper()}


def _ec_key(kid):
    return {"kty": "EC", "crv": "P-256", "kid": kid,
            "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
            "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0"}


def _coordinator(**options):
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True, **options))
    n = [0]

    def join(frm, kind, workspace="w", **extra):
        n[0] += 1
        return c.dispatch({"jsonrpc": "2.0", "id": f"j{n[0]}", "method": "participant.join",
                           "params": {"workspace": workspace, "from": frm, "type": kind, **extra}})

    return c, join


def _keys_of(c, uri):
    return [k.kid for k in c.workspaces["w"].members[uri].keys]


# ------------------------------------------------------------- types and URIs

def test_a_participant_is_one_of_five_types_and_its_uris_scheme_names_the_type():
    c, join = _coordinator()
    for kind in ("human", "agent", "service"):
        assert join(f"{kind}:x", kind)["result"] == {"joined": True, "as": f"{kind}:x"}
    # group: and workspace: name address scopes, which a review's `to` uses;
    # they do not join.
    for kind in ("group", "workspace"):
        assert join(f"{kind}:x", kind)["error"] == {
            "code": -32602,
            "message": "group: and workspace: URIs name address scopes, which cannot join"}
    assert join("bot:x", "bot")["error"] == {
        "code": -32602, "message": "type must be human, agent, service, group or workspace"}
    assert join("human:mallory", "agent")["error"] == {
        "code": -32602, "message": 'from must start with "agent:" for type agent'}
    assert join("alice", "human")["error"] == {
        "code": -32602, "message": 'from must start with "human:" for type human'}
    assert len(c.workspaces["w"].members) == 3


def test_a_credential_of_the_wrong_type_is_refused():
    _, join = _coordinator()
    assert join("human:a", "human", oidc_token={"t": 1})["error"] == {
        "code": -32602, "message": "oidc_token must be a string"}
    assert join("human:a", "human", vc_presentation="vp")["error"] == {
        "code": -32602, "message": "vc_presentation must be an object"}


# --------------------------------------------------- identity required of people

TOKENS = {"alice": {"sub": "alice", "chap_participant_uri": "human:alice",
                    "cnf": {"jwk": _key("alice-k1")}},
          "bob": {"sub": "bob", "chap_participant_uri": "human:bob"}}
PRESENTATIONS = {"carol": {"holder": "did:example:carol", "cnf_jwk": _key("carol-vp")}}


def _verify_oidc_token(t):
    return TOKENS.get(t)


def _verify_vc(vp):
    return PRESENTATIONS.get(vp.get("who"))


def test_where_a_token_verifier_is_configured_and_advertised_a_person_joins_with_a_token():
    c, join = _coordinator(verify_oidc_token=_verify_oidc_token)
    assert join("human:alice", "human")["error"] == {
        "code": -32403, "message": "An oidc_token is required to join as a human participant"}
    # The join would have made the workspace, and made nothing.
    assert "w" not in c.workspaces
    assert "error" not in join("human:alice", "human", oidc_token="alice")
    assert "error" not in join("agent:bot", "agent")
    # A member joining again is a join like any other.
    assert join("human:alice", "human")["error"]["code"] == -32403


def test_where_a_presentation_verifier_is_configured_and_advertised_a_person_joins_with_one():
    _, join = _coordinator(verify_vc=_verify_vc,
                           default_profiles=["core/1.0", "review/1.0", "identity-vc/1.0"])
    assert join("human:carol", "human")["error"] == {
        "code": -32410, "message": "A vc_presentation is required to join as a human participant"}
    assert "error" not in join("human:carol", "human", vc_presentation={"who": "carol"})


def test_where_both_apply_either_credential_admits_a_person():
    _, join = _coordinator(verify_oidc_token=_verify_oidc_token, verify_vc=_verify_vc,
                           default_profiles=["core/1.0", "review/1.0", "identity-vc/1.0"])
    assert join("human:dan", "human")["error"] == {
        "code": -32403,
        "message": "An oidc_token or a vc_presentation is required to join as a human participant"}
    assert "error" not in join("human:carol", "human", vc_presentation={"who": "carol"})
    assert "error" not in join("human:bob", "human", oidc_token="bob")


def test_a_presentation_verifier_makes_a_workspace_advertise_identity_vc_and_the_profile_needs_one():
    c, join = _coordinator(verify_vc=_verify_vc)
    assert join("human:carol", "human")["error"]["code"] == -32410
    assert "error" not in join("human:carol", "human", vc_presentation={"who": "carol"})
    assert "identity-vc/1.0" in c.workspaces["w"].profiles
    bare = Coordinator(CoordinatorOptions())
    assert bare.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                          "params": {"workspace": "v",
                                     "profiles": ["core/1.0", "identity-vc/1.0"]}})["error"] == {
        "code": -32602,
        "message": "identity-vc/1.0 is advertised but no presentation verifier is configured. "
                   "A workspace that advertises it and does not enforce it describes a "
                   "guarantee it does not give."}


def test_a_workspace_that_does_not_advertise_the_profile_takes_a_person_without_one():
    # Made before the coordinator had a verifier, it says nothing of its people.
    store = MemoryStore()
    Coordinator(CoordinatorOptions(store=store)).dispatch(
        {"jsonrpc": "2.0", "id": "c", "method": "workspace.create", "params": {"workspace": "w"}})
    c = Coordinator(CoordinatorOptions(store=store, verify_vc=_verify_vc))
    r = c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "w", "from": "human:carol", "type": "human"}})
    assert "error" not in r


# ------------------------------------------------------- credentials off the chain

def test_a_joins_token_and_presentation_are_recorded_as_their_digests():
    c, join = _coordinator(verify_oidc_token=_verify_oidc_token, verify_vc=_verify_vc,
                           enable_chain=True,
                           default_profiles=["core/1.0", "review/1.0", "identity-vc/1.0"])
    vp = {"who": "carol", "proof": {"value": "secret"}}
    join("human:bob", "human", oidc_token="bob")
    join("human:carol", "human", vc_presentation=vp)
    bob, carol = (e.envelope["params"] for e in c.workspaces["w"].audit)
    assert bob["oidc_token"] == "sha256:" + hashlib.sha256(b"bob").hexdigest()
    assert carol["vc_presentation"] == "sha256:" + hashlib.sha256(canonicalize(vp)).hexdigest()
    assert "secret" not in json.dumps([e.to_dict() for e in c.workspaces["w"].audit])
    verified = c.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                           "params": {"workspace": "w"}})
    assert verified["result"]["status"] == "verified"


def test_a_signed_join_with_a_token_takes_effect_once():
    c, _ = _coordinator(verify_oidc_token=_verify_oidc_token)
    envelope = {"jsonrpc": "2.0", "id": "s", "method": "participant.join",
                "sig": "ed25519:k:" + "A" * 86 + "==",
                "params": {"workspace": "w", "from": "human:bob", "type": "human",
                           "oidc_token": "bob"}}
    assert "error" not in c.dispatch(envelope)
    again = c.dispatch(envelope)
    assert again["error"]["code"] == -32075
    assert again["error"]["data"] == {"accepted_at_seq": 0}


# ----------------------------------------------------------- the nonce binding

MISMATCH = {"code": -32404, "message": "The join's keys are not the key the token binds"}


def test_the_key_whose_thumbprint_is_the_nonce_is_bound_and_a_join_bringing_another_key_is_refused():
    assert jwk_thumbprint(RFC8037_KEY) == "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"
    tokens = {"okp": {"sub": "erin", "chap_participant_uri": "human:erin",
                      "nonce": jwk_thumbprint(_key("erin-1"))},
              "ec": {"sub": "fay", "chap_participant_uri": "human:fay",
                     "nonce": jwk_thumbprint(_ec_key("fay-1"))}}
    c, join = _coordinator(verify_oidc_token=tokens.get)
    assert join("human:erin", "human", oidc_token="okp",
                jwks={"keys": [_key("erin-2"), _key("erin-1")]})["error"] == MISMATCH
    join("human:erin", "human", oidc_token="okp", jwks={"keys": [_key("erin-1")]})
    assert _keys_of(c, "human:erin") == ["erin-1"]
    join("human:fay", "human", oidc_token="ec", jwks={"keys": [_ec_key("fay-1")]})
    assert _keys_of(c, "human:fay") == ["fay-1"]


def test_a_nonce_or_cnf_jwk_the_joins_keys_are_not_is_refused_and_one_with_no_kid_takes_its_thumbprint():
    unnamed = {k: v for k, v in _key("k-cnf").items() if k != "kid"}
    tokens = {"none": {"sub": "g", "chap_participant_uri": "human:g", "nonce": "not-a-thumbprint"},
              "cnf": {"sub": "i", "chap_participant_uri": "human:i",
                      "nonce": jwk_thumbprint(_key("i-1")), "cnf": {"jwk": _key("i-cnf")}},
              "unnamed": {"sub": "k", "chap_participant_uri": "human:k", "cnf": {"jwk": unnamed}}}
    c, join = _coordinator(verify_oidc_token=tokens.get)
    assert join("human:g", "human", oidc_token="none",
                jwks={"keys": [_key("g-1")]})["error"] == MISMATCH
    assert join("human:i", "human", oidc_token="cnf",
                jwks={"keys": [_key("i-1")]})["error"] == MISMATCH
    join("human:i", "human", oidc_token="cnf")
    assert _keys_of(c, "human:i") == ["i-cnf"]
    thumbprint = jwk_thumbprint(unnamed)
    assert join("human:k", "human", oidc_token="unnamed",
                jwks={"keys": [_key("k-cnf")]})["error"] == MISMATCH
    assert "error" not in join("human:k", "human", oidc_token="unnamed",
                               jwks={"keys": [{**unnamed, "kid": thumbprint}]})
    assert _keys_of(c, "human:k") == [thumbprint]


def test_a_person_a_token_verifies_has_no_self_asserted_key_and_the_token_names_the_person():
    tokens = {"named": {"sub": "m", "chap_participant_uri": "human:m"},
              "anyone": {"sub": "mallory"}}
    c, join = _coordinator(verify_oidc_token=tokens.get)
    assert join("human:m", "human", oidc_token="named", jwks={"keys": [_key("m-1")]})["error"] == {
        "code": -32404,
        "message": "The token binds no key: a person it verifies signs with a key it binds"}
    # Any user of the provider could otherwise take any free name.
    assert join("human:ceo", "human", oidc_token="anyone")["error"] == {
        "code": -32404, "message": "OIDC token does not name this participant"}
    assert "w" not in c.workspaces
    assert "error" not in join("human:m", "human", oidc_token="named")
    assert _keys_of(c, "human:m") == []


def test_a_member_joining_again_with_a_key_its_token_binds_adds_it():
    tokens = {"first": {"sub": "jo", "chap_participant_uri": "human:jo",
                        "cnf": {"jwk": _key("jo-1")}},
              "second": {"sub": "jo", "nonce": jwk_thumbprint(_key("jo-2"))}}
    c, join = _coordinator(verify_oidc_token=tokens.get)
    join("human:jo", "human", oidc_token="first")
    assert join("human:jo", "human", oidc_token="second",
                jwks={"keys": [_key("jo-2"), _key("jo-3")]})["error"] == MISMATCH
    r = join("human:jo", "human", oidc_token="second", jwks={"keys": [_key("jo-2")]})
    assert "error" not in r
    assert _keys_of(c, "human:jo") == ["jo-1", "jo-2"]
