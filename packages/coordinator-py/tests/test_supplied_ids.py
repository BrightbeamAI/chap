"""
A supplied whisper, deliberation or handoff id names a new one.

``whisper.ask``, ``deliberate.open`` and ``handoff.propose`` take an optional
id, and stored the new object under it whatever it held: a closed deliberation
could be opened again with its votes and outcome erased, an answered whisper
reset, and a pending handoff replaced. An id already in use, one that is not
a string, or a name JavaScript treats specially as an object key, is now
refused with -32602, and nothing changes. The TypeScript coordinator dropped
an object stored under __proto__ when it saved the workspace, and both
coordinators refuse the same ids.

Mirrors packages/coordinator/tests/supplied_ids.test.ts.
"""
from __future__ import annotations

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.jsonrpc import E

PROFILES = ["core/1.0", "whisper/1.0", "deliberation/1.0", "handoff/1.0"]


def _ready():
    c = Coordinator(CoordinatorOptions(default_profiles=PROFILES, deterministic_ids=True))

    def send(method, actor="human:a", **params):
        return c.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                           "params": {"workspace": "w", "from": actor, **params}})

    send("workspace.create", profiles=PROFILES)
    for uri, kind in (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent")):
        send("participant.join", actor=uri, type=kind)
    task = send("task.create", kind="k", input={}, assignee="agent:b")["result"]["task_id"]
    return c, send, task


def _ws(c):
    return c.get_workspace("w")


def test_a_closed_deliberation_cannot_be_opened_again_under_its_id():
    c, send, _ = _ready()
    opened = send("deliberate.open", deliberation_id="d1", to=["human:a", "human:c"],
                  rule="any_one_approves", question="q")
    assert opened["result"]["deliberation_id"] == "d1"
    send("deliberate.vote", deliberation_id="d1", vote="yea")
    send("deliberate.vote", actor="human:c", deliberation_id="d1", vote="yea")
    send("deliberate.close", deliberation_id="d1")
    outcome = _ws(c).deliberations["d1"].outcome
    assert outcome

    again = send("deliberate.open", deliberation_id="d1", to=["human:a"],
                 rule="any_one_approves", question="q2")
    assert again["error"]["code"] == E.PARAMS
    assert "deliberation_id already in use: d1" in again["error"]["message"]
    assert _ws(c).deliberations["d1"].outcome == outcome


def test_an_answered_whisper_cannot_be_reset_under_its_id():
    c, send, task = _ready()
    ask = dict(task_id=task, to="human:a", question="q", deadline_ms=60_000, default_if_lapsed="x")
    assert send("whisper.ask", actor="agent:b", whisper_id="w1", **ask)["result"]["whisper_id"] == "w1"
    send("whisper.answer", whisper_id="w1", answer="yes")
    assert _ws(c).whispers["w1"].state == "answered"

    again = send("whisper.ask", actor="agent:b", whisper_id="w1", **ask)
    assert again["error"]["code"] == E.PARAMS
    assert "whisper_id already in use: w1" in again["error"]["message"]
    assert _ws(c).whispers["w1"].state == "answered"


def test_a_pending_handoff_cannot_be_replaced_under_its_id():
    c, send, task = _ready()
    first = send("handoff.propose", actor="agent:b", handoff_id="h1", to="human:c",
                 tasks=[{"task_id": task}])
    assert first["result"]["handoff_id"] == "h1"
    again = send("handoff.propose", actor="agent:b", handoff_id="h1", to="human:a",
                 tasks=[{"task_id": task}])
    assert again["error"]["code"] == E.PARAMS
    assert "handoff_id already in use: h1" in again["error"]["message"]
    assert _ws(c).handoffs["h1"].recipient == "human:c"


def test_an_id_that_is_not_a_string_is_refused():
    _, send, task = _ready()
    r = send("whisper.ask", actor="agent:b", task_id=task, to="human:a", question="q",
             deadline_ms=1, default_if_lapsed="x", whisper_id=7)
    assert r["error"]["code"] == E.PARAMS
    assert "whisper_id must be a string" in r["error"]["message"]


def test_a_name_javascript_treats_specially_as_an_object_key_is_refused_as_an_id():
    c, send, task = _ready()
    for ident in ("__proto__", "constructor", "prototype"):
        d = send("deliberate.open", deliberation_id=ident, to=["human:a"], rule="any_one_approves", question="q")
        assert d["error"]["code"] == E.PARAMS, ident
        assert d["error"]["message"] == f'deliberation_id cannot be "{ident}"'
        w = send("whisper.ask", actor="agent:b", task_id=task, to="human:a", question="q",
                 deadline_ms=60_000, default_if_lapsed="x", whisper_id=ident)
        assert w["error"]["code"] == E.PARAMS, ident
        assert w["error"]["message"] == f'whisper_id cannot be "{ident}"'
        h = send("handoff.propose", actor="agent:b", handoff_id=ident, to="human:c", tasks=[{"task_id": task}])
        assert h["error"]["code"] == E.PARAMS, ident
        assert h["error"]["message"] == f'handoff_id cannot be "{ident}"'
    assert len(_ws(c).deliberations) == 0
    assert len(_ws(c).whispers) == 0
    assert len(_ws(c).handoffs) == 0


def test_without_an_id_or_with_an_empty_one_an_id_is_generated_as_before():
    _, send, _ = _ready()
    a = send("deliberate.open", to=["human:a"], rule="any_one_approves", question="q")
    b = send("deliberate.open", deliberation_id="", to=["human:a"], rule="any_one_approves", question="q")
    assert a["result"]["deliberation_id"]
    assert b["result"]["deliberation_id"]
    assert a["result"]["deliberation_id"] != b["result"]["deliberation_id"]
