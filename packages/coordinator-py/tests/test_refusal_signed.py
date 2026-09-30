"""SPECIFICATION 10.1: what happens when a signed request is sent again.

The log publishes every recorded call, signature and all, so anyone who can
read it holds a copy of each signed request. A copy of a recorded refusal is
answered with that refusal and not evaluated, even once whatever refused it
has changed. A copy of a call that took effect is evaluated as any request
is, and if it is refused the refusal is not recorded, because its signer made
that call once. Both rules compare what the sender signed, the request
without its ``sig``, so re-encoding a signature does not make a new request.

The mirror of this file is
packages/coordinator/tests/refusal_signed.test.ts.
"""
from __future__ import annotations

import copy
import itertools
import json

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import derive_private_key, public_jwk, sign, verify
from chap_coordinator.storage.store import MemoryStore

PROFILES = ["core/1.0", "review/1.0", "control/1.0", "security-signed/1.0", "audit-scitt/1.0"]
PEOPLE = [("human:a", "human"), ("human:c", "human"), ("agent:b", "agent")]


class _Signed:
    """A workspace that requires signatures, with a signing helper per member."""

    def __init__(self, store=None):
        self.coord = Coordinator(CoordinatorOptions(
            require_signatures=True, deterministic_ids=True, deterministic_clock=True,
            default_profiles=PROFILES, **({"store": store} if store is not None else {})))
        self.keys = {uri: derive_private_key(uri) for uri, _ in PEOPLE}
        self._ids = itertools.count(1)
        self.coord.dispatch({"jsonrpc": "2.0", "id": "create", "method": "workspace.create",
                             "params": {"workspace": "w", "from": "human:a",
                                        "profiles": PROFILES}})
        for uri, kind in PEOPLE:
            self.coord.dispatch({"jsonrpc": "2.0", "id": f"join-{uri}",
                                 "method": "participant.join",
                                 "params": {"workspace": "w", "from": uri, "type": kind,
                                            "jwks": {"keys": [public_jwk(uri, self.keys[uri])]}}})
        self.ws = self.coord.get_workspace("w")

    def envelope(self, method, params=None, actor="human:a"):
        env = {"jsonrpc": "2.0", "id": f"s{next(self._ids)}", "method": method,
               "params": {"workspace": "w", "from": actor, **(params or {})}}
        env["sig"] = sign(canonicalize(env), self.keys[actor],
                          public_jwk(actor, self.keys[actor])["kid"])
        return env

    def send(self, method, params=None, actor="human:a"):
        return self.coord.dispatch(self.envelope(method, params, actor))

    def copy(self, env):
        return self.coord.dispatch(copy.deepcopy(env))

    def under_review(self, to):
        tid = self.send("task.create", {"kind": "k", "input": {}, "assignee": "agent:b"},
                        "agent:b")["result"]["task_id"]
        self.send("task.update", {"task_id": tid, "state": "in_progress"}, "agent:b")
        self.send("review.request", {"task_id": tid, "artefact": {"body": "draft"}, "to": to},
                  "agent:b")
        return tid


def _reencoded(sig: str) -> str:
    """The same signature in a different encoding.

    A 64-byte signature leaves four unused bits in its last base64
    character, and a lenient decoder ignores them.
    """
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    i = len(sig) - 3
    return sig[:i] + alphabet[alphabet.index(sig[i]) | 1] + sig[i + 1:]


def test_a_signed_copy_of_a_recorded_refusal_is_answered_with_it_even_once_it_would_pass():
    w = _Signed()
    tid = w.under_review("human:c")
    late = w.envelope("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    assert w.copy(late)["error"]["code"] == -32011
    at = len(w.ws.audit) - 1
    w.send("review.request", {"task_id": tid, "artefact": {"body": "draft"}, "to": "human:a"},
           "agent:b")
    before = len(w.ws.audit)

    replay = w.copy(late)
    assert replay["error"] == {
        "code": -32011,
        "message": f"Refused at seq {at}; a refused request is not evaluated again",
        "data": {"refused_at_seq": at},
    }
    assert len(w.ws.audit) == before, "the copy was recorded"
    assert w.ws.tasks[tid].state == "review_requested", "the copy took effect"

    # A retry is a new request: a new id, signed afresh.
    assert "error" not in w.send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")


def test_re_encoding_the_signature_does_not_make_a_new_request():
    w = _Signed()
    tid = w.under_review("human:c")
    late = w.envelope("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    w.copy(late)
    at = len(w.ws.audit) - 1
    w.send("review.request", {"task_id": tid, "artefact": {"body": "draft"}, "to": "human:a"},
           "agent:b")

    variant = {**late, "sig": _reencoded(late["sig"])}
    assert variant["sig"] != late["sig"]
    # The variant verifies, so only the rule stands between it and the decision.
    unsigned = {k: v for k, v in variant.items() if k != "sig"}
    assert verify(canonicalize(unsigned), variant["sig"], w.keys["human:a"].public_key())
    assert w.copy(variant)["error"]["data"] == {"refused_at_seq": at}
    assert w.ws.tasks[tid].state == "review_requested"


def test_a_copy_of_a_refusal_on_a_paused_workspace_does_not_take_effect_once_it_resumes():
    w = _Signed()
    w.send("control.pause", {"scope": "workspace", "reason": "incident"}, "human:a")
    create = w.envelope("task.create", {"kind": "k", "input": {}, "assignee": "agent:b"},
                        "agent:b")
    assert w.copy(create)["error"]["code"] == -32063
    at = len(w.ws.audit) - 1
    w.send("control.resume", {"scope": "workspace", "reason": "clear"}, "human:a")
    tasks = len(w.ws.tasks)

    assert w.copy(create)["error"] == {
        "code": -32063,
        "message": f"Refused at seq {at}; a refused request is not evaluated again",
        "data": {"refused_at_seq": at},
    }
    assert len(w.ws.tasks) == tasks


def test_the_answer_comes_before_the_pause_and_before_the_signature_check():
    w = _Signed()
    tid = w.under_review("human:c")
    late = w.envelope("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    w.copy(late)
    at = len(w.ws.audit) - 1
    w.send("control.pause", {"scope": "workspace", "reason": "incident"}, "human:c")
    assert w.copy(late)["error"]["data"] == {"refused_at_seq": at}
    w.ws.members["human:a"].keys[0].revoked_at = "2000-01-01T00:00:00.000Z"
    assert w.copy(late)["error"]["data"] == {"refused_at_seq": at}


def test_a_signed_copy_of_an_accepted_call_that_is_refused_is_not_recorded():
    w = _Signed()
    tid = w.under_review("human:c")
    approve = w.envelope("decide.approve", {"task_id": tid, "comment": "ok"}, "human:c")
    assert "error" not in w.copy(approve)
    before = len(w.ws.audit)
    # The review is closed, so the copy is refused. Its signer approved once.
    assert w.copy(approve)["error"]["code"] == -32010
    assert w.copy({**approve, "sig": _reencoded(approve["sig"])})["error"]["code"] == -32010
    assert len(w.ws.audit) == before


def test_the_rules_survive_a_restart():
    store = MemoryStore()
    w = _Signed(store=store)
    tid = w.under_review("human:c")
    late = w.envelope("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    w.copy(late)
    at = len(w.ws.audit) - 1
    approve = w.envelope("decide.approve", {"task_id": tid, "comment": "ok"}, "human:c")
    w.copy(approve)
    w.send("control.pause", {"scope": "workspace", "reason": "incident"}, "human:c")

    # The restarted coordinator runs on the real clock, which is past the
    # time the deterministic clock registered the keys at.
    restarted = Coordinator(CoordinatorOptions(
        require_signatures=True, default_profiles=PROFILES, store=store))
    ws = restarted.get_workspace("w")
    before = len(ws.audit)
    assert restarted.dispatch(json.loads(json.dumps(late)))["error"]["data"] == {
        "refused_at_seq": at}
    assert restarted.dispatch(json.loads(json.dumps(approve)))["error"]["code"] == -32063
    assert len(ws.audit) == before


def test_a_refusal_for_a_key_that_is_unknown_revoked_or_the_wrong_one_for_a_rotation_is_not_recorded():
    w = _Signed()
    before = len(w.ws.audit)

    stranger = derive_private_key("human:nobody")
    unknown = {"jsonrpc": "2.0", "id": "k1", "method": "task.create",
               "params": {"workspace": "w", "from": "human:a", "kind": "k", "input": {},
                          "assignee": "agent:b"}}
    unknown["sig"] = sign(canonicalize(unknown), stranger,
                          public_jwk("human:nobody", stranger)["kid"])
    assert w.coord.dispatch(unknown)["error"]["code"] == -32071

    other = public_jwk("human:a#second", derive_private_key("human:a#second"))
    rotate = w.send("participant.rotate_key", {"old_kid": other["kid"], "new_jwk": other},
                    "human:a")
    assert rotate["error"]["code"] == -32073

    w.ws.members["human:a"].keys[0].revoked_at = "2000-01-01T00:00:00.000Z"
    assert w.send("task.create", {"kind": "k", "input": {}, "assignee": "agent:b"},
                  "human:a")["error"]["code"] == -32072
    assert len(w.ws.audit) == before


def test_an_unsigned_refusal_does_not_answer_a_signed_call_with_the_same_content():
    # Only signed calls are indexed, so a refusal anyone could have sent in
    # the member's name cannot stand in for the member's signed call.
    from chap_coordinator.types import AuditEntry
    store = MemoryStore()
    w = _Signed(store=store)
    tid = w.under_review("human:c")
    late = w.envelope("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    unsigned = {k: v for k, v in late.items() if k != "sig"}
    w.ws.audit.append(AuditEntry(seq=len(w.ws.audit), arrived="2026-01-01T00:00:00.000Z",
                                 request=unsigned,
                                 outcome={"status": "refused", "code": -32011}))
    w.coord._persist(w.ws)
    restarted = Coordinator(CoordinatorOptions(
        require_signatures=True, default_profiles=PROFILES, store=store))
    r = restarted.dispatch(copy.deepcopy(late))
    assert r["error"]["code"] == -32011
    assert "data" not in r["error"], "the unsigned refusal answered the signed call"


def test_a_signed_call_that_cannot_be_canonicalised_is_refused_first_not_answered_from_the_log():
    w = _Signed()
    env = {"jsonrpc": "2.0", "id": "f", "method": "task.create",
           "params": {"workspace": "w", "from": "human:a", "kind": "k",
                      "input": {"weight": 1.5}, "assignee": "agent:b"},
           "sig": "ed25519:" + public_jwk("human:a", w.keys["human:a"])["kid"] + ":"
                  + "A" * 86 + "=="}
    before = len(w.ws.audit)
    assert w.coord.dispatch(env)["error"]["code"] == -32602
    assert len(w.ws.audit) == before
