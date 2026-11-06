"""
-32603 carries no exception text; the operator gets it through a hook.

A handler's exception was answered with its text in ``data.detail``, and the
text can name a path, a query or a value from another workspace (29 October
review). The caller now learns only that the call failed, and
on_internal_error receives the exception, defaulting to stderr.

Mirrors packages/coordinator/tests/internal_error.test.ts.
"""
from __future__ import annotations

import copy

from chap_coordinator import Coordinator, CoordinatorOptions

CALL = {"jsonrpc": "2.0", "id": "2", "method": "task.create",
        "params": {"workspace": "w", "from": "human:a", "kind": "k", "input": {}}}


def _failing(**options):
    c = Coordinator(CoordinatorOptions(**options))
    c.dispatch({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                "params": {"workspace": "w"}})

    def raiser(params):
        raise RuntimeError("secret path /srv/app/db.sqlite")

    c._handlers["task.create"] = raiser
    return c


def test_the_caller_is_answered_with_32603_and_no_detail():
    seen = []
    c = _failing(on_internal_error=lambda exc, call: seen.append((exc, call)))
    assert c.dispatch(copy.deepcopy(CALL)) == {
        "jsonrpc": "2.0", "id": "2", "error": {"code": -32603, "message": "Internal error"}}
    assert len(seen) == 1
    exc, call = seen[0]
    assert str(exc) == "secret path /srv/app/db.sqlite"
    assert call == {"method": "task.create", "workspace": "w"}


def test_without_a_hook_the_traceback_goes_to_stderr(capsys):
    c = _failing()
    assert c.dispatch(copy.deepcopy(CALL))["error"]["code"] == -32603
    err = capsys.readouterr().err
    assert "task.create failed with an internal error" in err
    assert "secret path /srv/app/db.sqlite" in err
    assert "Traceback" in err


def test_a_hook_that_raises_does_not_break_dispatch():
    def hook(exc, call):
        raise RuntimeError("hook failed")

    c = _failing(on_internal_error=hook)
    assert c.dispatch(copy.deepcopy(CALL))["error"]["code"] == -32603
