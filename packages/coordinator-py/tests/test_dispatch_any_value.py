"""
dispatch answers whatever it is given, and never raises.

Anything that is not a JSON-RPC call is answered with -32600, and a response's
id is null where the request carries none. The TypeScript reference read `id`
from a body of `null` and threw; this reference already answered, and is held
to the same answers here.

Mirrors packages/coordinator/tests/dispatch_any_value.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator

INVALID = {"code": -32600, "message": "Invalid JSON-RPC 2.0 request"}


def test_a_value_that_is_not_a_call_is_answered_with_32600_and_a_null_id():
    c = Coordinator()
    for value in (None, [], "x", 5, True, {}, {"jsonrpc": "1.0", "method": "x"}):
        assert c.dispatch(value) == {"jsonrpc": "2.0", "id": None, "error": INVALID}, value


def test_an_invalid_call_keeps_the_id_it_carries():
    c = Coordinator()
    assert c.dispatch({"jsonrpc": "2.0", "id": "x1"}) == {
        "jsonrpc": "2.0", "id": "x1", "error": INVALID}


def test_a_call_with_no_id_is_answered_with_a_null_id():
    c = Coordinator()
    r = c.dispatch({"jsonrpc": "2.0", "method": "workspace.create", "params": {"workspace": "w"}})
    assert r["id"] is None
    assert "result" in r
