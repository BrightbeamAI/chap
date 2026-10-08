"""Two input edges, answered alike by both references.

``audit.read`` reports ``next_seq`` as the next entry to read. A ``to_seq`` past
the end of the log was returned as is, so a reader paging forward from it
skipped every entry written after its read.

Under required signatures the key is chosen by the ``ts`` the sender gives. A
``ts`` that is not a string gives no time: TypeScript chose a key anyway and
Python raised out of ``dispatch``. Both now refuse it with ``-32070``.

Mirrors packages/coordinator/tests/read_paging_and_signed_ts.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions


def test_audit_read_never_reports_a_next_seq_past_the_end_of_the_log():
    c = Coordinator(CoordinatorOptions(deterministic_ids=True, deterministic_clock=True))

    def send(method, **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", **params}})

    send("workspace.create", profiles=["core/1.0"])
    send("participant.join", **{"from": "human:a", "type": "human"})
    send("participant.join", **{"from": "agent:b", "type": "agent"})

    def length():
        return len(c.workspaces["w"].audit)

    past = send("audit.read", range={"from_seq": 0, "to_seq": 1000})["result"]
    assert past["next_seq"] == length()
    assert len(past["entries"]) == length()

    within = send("audit.read", range={"from_seq": 0, "to_seq": 1})["result"]
    assert within["next_seq"] == 1
    assert send("audit.read")["result"]["next_seq"] == length()

    # A reader that pages on from next_seq meets the entry written after it.
    send("task.create", **{"from": "human:a", "kind": "k", "input": {}, "assignee": "agent:b"})
    nxt = send("audit.read", range={"from_seq": past["next_seq"]})["result"]
    assert len(nxt["entries"]) == 1
    assert nxt["entries"][0]["envelope"]["method"] == "task.create"


def test_a_signed_call_whose_ts_is_not_a_string_is_refused_with_32070():
    c = Coordinator(CoordinatorOptions(require_signatures=True,
                                       deterministic_ids=True, deterministic_clock=True))

    def send(sig=None, **params):
        env = {"jsonrpc": "2.0", "id": "x", "method": "task.create",
               "params": {"workspace": "w", "from": "human:a", "kind": "k", "input": {},
                          "assignee": "human:a", **params}}
        if sig:
            env["sig"] = sig
        return c.dispatch(env)

    c.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                "params": {"workspace": "w", "profiles": ["core/1.0"]}})
    c.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join", "params": {
        "workspace": "w", "from": "human:a", "type": "human",
        "jwks": {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "k1",
                           "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"}]}}})
    sig = "ed25519:k1:AAAA"

    for ts in (5, True, {"at": "now"}, ["2026-01-01T00:00:00Z"]):
        r = send(sig, ts=ts)
        assert r["error"]["code"] == -32070, ts
        assert r["error"]["message"] == "Cannot verify signature: ts must be a string"
    # An empty ts is a time no key covers.
    empty = send(sig, ts="")
    assert empty["error"]["code"] == -32071
    assert empty["error"]["message"] == "No key k1 valid at  for human:a"
    # No ts at all falls back to the coordinator's clock, which finds the key,
    # and this signature then fails to verify.
    none = send(sig)
    assert none["error"]["code"] == -32070
    assert none["error"]["message"] == "Signature failed verification"
    assert len(c.workspaces["w"].tasks) == 0
