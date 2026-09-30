"""SPECIFICATION 10.1: a refused call that is a governed attempt is recorded.

The attempt sits on the log under ``request``, with an ``outcome`` beside
it, so a reader keyed on ``envelope`` passes it by instead of replaying it.
The chain link hashes the outcome together with the request, so neither
half can be altered or stripped undetected.

The mirror of this file is
packages/coordinator/tests/refusal_record.test.ts.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.catalogue import ALWAYS_AVAILABLE, OWNING_PROFILE
from chap_coordinator.coordinator import PRIVILEGED_METHODS
from chap_coordinator.canonical import canonicalize
from chap_coordinator.crypto import derive_private_key, public_jwk, sign, verify
from chap_coordinator.storage.store import MemoryStore

CHAINED = ["core/1.0", "review/1.0", "control/1.0", "audit-scitt/1.0"]


def _ready(profiles=CHAINED, **options):
    coord = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True,
        default_profiles=profiles, **options))

    def send(method, params=None, actor="human:a"):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"id-{method}", "method": method,
                               "params": {"workspace": "w", "from": actor, **(params or {})}})

    send("workspace.create", {"profiles": profiles})
    for uri, kind in (("human:a", "human"), ("human:c", "human"), ("agent:b", "agent")):
        send("participant.join", {"type": kind}, uri)
    return coord, send, coord.get_workspace("w")


def _under_review(send) -> str:
    """A task drafted by agent:b and put up for review to human:c alone."""
    tid = send("task.create", {"kind": "k", "input": {}, "assignee": "agent:b"},
               "agent:b")["result"]["task_id"]
    send("task.update", {"task_id": tid, "state": "in_progress"}, "agent:b")
    send("review.request", {"task_id": tid, "artefact": {"body": "draft"}, "to": "human:c"},
         "agent:b")
    return tid


# ------------------------------------------------------------ what is recorded

def test_an_unauthorised_decision_is_recorded_as_a_refusal():
    _, send, ws = _ready()
    tid = _under_review(send)
    before = len(ws.audit)
    r = send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    assert r["error"]["code"] == -32011
    assert len(ws.audit) == before + 1
    entry = ws.audit[before]
    assert entry.envelope is None
    assert entry.request["method"] == "decide.approve"
    assert entry.outcome == {"status": "refused", "code": -32011}


def test_acting_on_a_paused_workspace_is_recorded_as_a_refusal():
    _, send, ws = _ready()
    send("control.pause", {"scope": "workspace", "reason": "incident"})
    before = len(ws.audit)
    r = send("task.create", {"kind": "k", "input": {}, "assignee": "agent:b"}, "agent:b")
    assert r["error"]["code"] == -32063
    assert len(ws.audit) == before + 1
    assert ws.audit[before].outcome == {"status": "refused", "code": -32063}


@pytest.mark.parametrize("method,params,actor", [
    ("whisper.ask", {"task_id": "t", "question": "?", "options": ["a"]}, "human:a"),
    ("nothing.here", {}, "human:a"),
    ("task.create", {"kind": "k"}, "human:a"),
    ("control.pause", {"task_id": "t", "reason": "hold"}, "human:stranger"),
    ("audit.verify_receipt", {"receipt": {}}, "human:a"),
], ids=["gate-refusal-of-an-ordinary-method", "method-that-does-not-exist",
        "invalid-parameters", "call-from-a-non-member", "refused-read"])
def test_not_recorded(method, params, actor):
    _, send, ws = _ready()
    before = len(ws.audit)
    r = send(method, params, actor)
    assert "error" in r, "the call under test was not refused"
    assert len(ws.audit) == before


def _signed_workspace(profiles):
    key = derive_private_key("human:a")
    jwk = public_jwk("human:a", key)
    coord = Coordinator(CoordinatorOptions(
        require_signatures=True, deterministic_ids=True, deterministic_clock=True,
        default_profiles=profiles))
    coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "w", "from": "human:a", "type": "human",
                               "jwks": {"keys": [jwk]}, "profiles": profiles}})
    return coord, key, jwk


def test_a_call_whose_signature_fails_is_not_recorded():
    coord, key, jwk = _signed_workspace(["core/1.0", "review/1.0", "security-signed/1.0"])
    ws = coord.get_workspace("w")
    before = len(ws.audit)
    env = {"jsonrpc": "2.0", "id": "x", "method": "task.create",
           "params": {"workspace": "w", "from": "human:a", "kind": "k", "input": {},
                      "assignee": "human:a"}}
    env["sig"] = sign(canonicalize(env), key, jwk["kid"])
    env["params"]["kind"] = "altered after signing"
    r = coord.dispatch(env)
    assert r["error"]["code"] == -32070
    assert len(ws.audit) == before


def test_the_refused_request_is_kept_as_it_arrived_signature_and_all():
    profiles = ["core/1.0", "review/1.0", "security-signed/1.0", "audit-scitt/1.0"]
    coord, key, jwk = _signed_workspace(profiles)
    # control/1.0 is not advertised, so the gate refuses a privileged method.
    env = {"jsonrpc": "2.0", "id": "x", "method": "control.pause",
           "params": {"workspace": "w", "from": "human:a", "task_id": "tsk_absent",
                      "reason": "hold"}}
    env["sig"] = sign(canonicalize(env), key, jwk["kid"])
    sent = copy.deepcopy(env)
    r = coord.dispatch(env)
    assert r["error"]["code"] == -32601
    entry = coord.get_workspace("w").audit[-1]
    assert entry.request == sent
    unsigned = {k: v for k, v in entry.request.items() if k != "sig"}
    assert verify(canonicalize(unsigned), entry.request["sig"], key.public_key())


def test_a_request_that_cannot_be_canonicalised_is_not_recorded():
    # The gate refuses before the ingress check, so this refusal reaches the
    # recording rule with a non-integer number in it. It cannot be hashed.
    _, send, ws = _ready(["core/1.0", "review/1.0", "audit-scitt/1.0"])
    before = len(ws.audit)
    r = send("control.pause", {"task_id": "t", "reason": "hold", "weight": 1.5})
    assert r["error"]["code"] == -32601
    assert len(ws.audit) == before


def test_the_privileged_methods_the_gate_can_refuse_agree_with_the_catalogue():
    # A gate refusal is recorded only for a privileged method. The catalogue's
    # `privileged` flag and the coordinators' list differ on methods the gate
    # never refuses, and they must agree on every method it can.
    root = Path(__file__).resolve().parents[3]
    methods = json.loads((root / "schemas/profiles/chap-methods.schema.json").read_text(
        encoding="utf-8"))["examples"][0]["methods"]
    for method, owner in OWNING_PROFILE.items():
        if owner == "core/1.0" or method in ALWAYS_AVAILABLE:
            continue
        assert (method in PRIVILEGED_METHODS) == (methods[method].get("privileged") is True), method


# ------------------------------------------------------------ the chain

def test_a_chain_with_refusals_on_it_verifies():
    _, send, _ = _ready()
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:c")
    v = send("audit.verify_chain")
    assert "error" not in v, v.get("error")
    assert v["result"]["ok"] is True


def _change_code(e):
    e.outcome["code"] = -32602


def _strip_outcome(e):
    e.outcome = None


def _recast_as_accepted(e):
    e.envelope, e.request, e.outcome = e.request, None, None


def _alter_request(e):
    e.request["params"]["comment"] = "altered"


@pytest.mark.parametrize("tamper", [_change_code, _strip_outcome, _recast_as_accepted,
                                    _alter_request],
                         ids=["changing-the-refusal-code", "stripping-the-outcome",
                              "recasting-the-refusal-as-an-accepted-call",
                              "altering-the-refused-request"])
def test_tampering_with_a_refusal_breaks_the_chain(tamper):
    _, send, ws = _ready()
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    refusal = next((e for e in ws.audit if e.outcome is not None), None)
    assert refusal is not None, "no refusal was recorded"
    tamper(refusal)
    assert "error" in send("audit.verify_chain")


# ------------------------------------------------------------ readers

def test_audit_read_returns_refusals_under_request_and_outcome_narrows():
    _, send, _ = _ready()
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")

    everything = send("audit.read")["result"]["entries"]
    refused = send("audit.read", {"filter": {"outcome": "refused"}})["result"]["entries"]
    accepted = send("audit.read", {"filter": {"outcome": "accepted"}})["result"]["entries"]
    assert len(refused) == 1
    assert len(accepted) + len(refused) == len(everything)
    assert sorted(refused[0]) == ["arrived", "outcome", "prev_hash", "request", "seq"]
    assert all("envelope" in e and "request" not in e for e in accepted)

    by_method = send("audit.read", {"filter": {"method": "decide.approve"}})["result"]["entries"]
    assert len(by_method) == 1
    assert by_method[0]["request"]["params"]["from"] == "human:a"
    by_task = send("audit.read", {"filter": {"task_id": tid, "outcome": "refused"}})["result"]["entries"]
    assert len(by_task) == 1


def test_an_unknown_outcome_filter_is_refused():
    _, send, _ = _ready()
    r = send("audit.read", {"filter": {"outcome": "maybe"}})
    assert r["error"]["code"] == -32602
    assert r["error"]["message"] == "filter.outcome must be 'accepted' or 'refused'"


def test_a_scitt_statement_for_a_refusal_carries_what_the_link_hashes():
    _, send, ws = _ready()
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    at = next(i for i, e in enumerate(ws.audit) if e.outcome is not None)
    r = send("audit.submit_to_scitt")
    e = ws.audit[at]
    assert r["result"]["statements"][at]["payload"] == canonicalize(
        {"outcome": e.outcome, "request": e.request}).decode("utf-8")


def test_listeners_hear_refusals():
    coord, send, _ = _ready()
    heard = []
    coord.add_audit_listener(lambda ws_id, entry: heard.append(entry))
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    assert heard[-1].request["method"] == "decide.approve"
    assert heard[-1].outcome == {"status": "refused", "code": -32011}


def test_refusals_survive_a_restart_and_the_chain_still_verifies():
    store = MemoryStore()
    _, send, ws = _ready(store=store)
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")

    restarted = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, default_profiles=CHAINED,
        store=store))
    after = restarted.get_workspace("w").audit
    assert [e.to_dict() for e in after] == [e.to_dict() for e in ws.audit]
    v = restarted.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                            "params": {"workspace": "w", "from": "human:a"}})
    assert "error" not in v, v.get("error")


# ------------------------------------------------------------ resubmission

def _late_approve(tid):
    return {"jsonrpc": "2.0", "id": "late", "method": "decide.approve",
            "params": {"workspace": "w", "from": "human:a", "task_id": tid, "comment": "ok"}}


def test_a_request_identical_to_a_recorded_refusal_is_answered_with_it_even_once_it_would_pass():
    # The log publishes a refused request, signature and all. Resubmitting the
    # same bytes after the review is re-addressed must not make it take effect.
    coord, send, ws = _ready()
    tid = _under_review(send)
    assert coord.dispatch(_late_approve(tid))["error"]["code"] == -32011
    at = len(ws.audit) - 1
    send("review.request", {"task_id": tid, "artefact": {"body": "draft"}, "to": "human:a"},
         "agent:b")
    before = len(ws.audit)

    replay = coord.dispatch(_late_approve(tid))
    assert replay["error"]["code"] == -32011
    assert replay["error"]["message"] == f"Refused at seq {at}; a refused request is not evaluated again"
    assert replay["error"]["data"] == {"refused_at_seq": at}
    assert len(ws.audit) == before, "the resubmission was recorded"
    assert ws.tasks[tid].state == "review_requested", "the resubmission took effect"

    fresh = coord.dispatch({**_late_approve(tid), "id": "fresh"})
    assert "error" not in fresh, fresh


def test_the_resubmission_rule_survives_a_restart():
    store = MemoryStore()
    coord, send, _ = _ready(store=store)
    tid = _under_review(send)
    coord.dispatch(_late_approve(tid))
    restarted = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, default_profiles=CHAINED, store=store))
    assert "refused_at_seq" in restarted.dispatch(_late_approve(tid))["error"]["data"]


# ------------------------------------------------------------ what stays off

def test_a_refused_join_is_not_recorded_even_from_a_members_uri():
    # participant.join is exempt from signature checks, so its refusal proves
    # nothing about who sent it.
    coord = Coordinator(CoordinatorOptions(
        require_signatures=True, deterministic_ids=True, deterministic_clock=True,
        verify_oidc_token=lambda token: None, default_profiles=["core/1.0", "review/1.0"]))
    jwk = public_jwk("human:a", derive_private_key("human:a"))
    coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "w", "from": "human:a", "type": "human",
                               "jwks": {"keys": [jwk]}}})
    ws = coord.get_workspace("w")
    before = len(ws.audit)
    r = coord.dispatch({"jsonrpc": "2.0", "id": "forged", "method": "participant.join",
                        "params": {"workspace": "w", "from": "human:a", "type": "human",
                                   "oidc_token": "forged"}})
    assert "error" in r
    assert len(ws.audit) == before


def test_a_method_the_catalogue_lists_but_no_coordinator_implements_is_not_recorded():
    _, send, ws = _ready()
    before = len(ws.audit)
    assert send("workspace.invite", {"invitee": "human:z"})["error"]["code"] == -32601
    assert len(ws.audit) == before


def test_a_refused_submit_to_scitt_is_not_recorded():
    _, send, ws = _ready()
    send("control.pause", {"scope": "workspace", "reason": "incident"})
    before = len(ws.audit)
    assert "error" in send("audit.submit_to_scitt")
    assert len(ws.audit) == before


def test_a_refusal_whose_code_is_not_an_integer_is_not_recorded():
    coord, send, ws = _ready()
    coord._handlers["custom.broken"] = lambda p: {"error": {"code": "bad", "message": "no"}}
    before = len(ws.audit)
    send("custom.broken")
    assert len(ws.audit) == before


def test_a_step_up_refusal_is_recorded():
    _, send, ws = _ready(enforce_step_up=True)
    before = len(ws.audit)
    r = send("control.pause", {"scope": "workspace", "reason": "incident"})
    assert r["error"]["code"] == -32402
    assert len(ws.audit) == before + 1
    assert ws.audit[before].outcome == {"status": "refused", "code": -32402}


def test_a_refused_notification_is_recorded_without_an_id():
    coord, send, ws = _ready()
    tid = _under_review(send)
    before = len(ws.audit)
    coord.dispatch({"jsonrpc": "2.0", "method": "decide.approve",
                    "params": {"workspace": "w", "from": "human:a", "task_id": tid,
                               "comment": "ok"}})
    assert len(ws.audit) == before + 1
    assert "id" not in ws.audit[before].request


# ------------------------------------------------------------ entry shape

def _move_record_under_envelope(e):
    e.envelope, e.request, e.outcome = {"outcome": e.outcome, "request": e.request}, None, None


def _hold_both(e):
    e.envelope = e.request


def _other_status(e):
    e.outcome["status"] = "accepted"


def _string_code(e):
    e.outcome["code"] = "-32011"


def _null_outcome(e):
    e.outcome = None


@pytest.mark.parametrize("tamper", [_move_record_under_envelope, _hold_both, _other_status,
                                    _string_code, _null_outcome],
                         ids=["moving-the-refusals-record-under-envelope",
                              "holding-both-an-envelope-and-a-request",
                              "an-outcome-of-another-status", "a-code-that-is-not-an-integer",
                              "a-null-outcome"])
def test_a_malformed_refusal_breaks_the_chain(tamper):
    _, send, ws = _ready()
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    refusal = next(e for e in ws.audit if e.outcome is not None)
    tamper(refusal)
    assert "error" in send("audit.verify_chain")


def test_adding_an_outcome_to_an_accepted_entry_breaks_the_chain():
    _, send, ws = _ready()
    ws.audit[-1].outcome = {"status": "refused", "code": -32011}
    assert "error" in send("audit.verify_chain")


# ------------------------------------------------------------ filters

def test_a_null_outcome_filter_reads_as_no_filter():
    _, send, _ = _ready()
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    everything = send("audit.read")["result"]["entries"]
    r = send("audit.read", {"filter": {"outcome": None}})
    assert "error" not in r, r
    assert len(r["result"]["entries"]) == len(everything)


def test_a_filter_that_is_not_an_object_reads_as_no_filter():
    _, send, _ = _ready()
    everything = send("audit.read")["result"]["entries"]
    assert len(send("audit.read", {"filter": "x"})["result"]["entries"]) == len(everything)


def test_from_matches_a_refused_calls_sender():
    _, send, _ = _ready()
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    mine = send("audit.read", {"filter": {"from": "human:a", "outcome": "refused"}})["result"]["entries"]
    assert len(mine) == 1


def test_a_refused_whisper_answer_with_a_malformed_whisper_id_leaves_task_filters_working():
    _, send, _ = _ready(CHAINED + ["whisper/1.0"])
    send("control.pause", {"scope": "workspace", "reason": "incident"})
    r = send("whisper.answer", {"whisper_id": {"x": 1}, "answer_option": "a"}, "human:c")
    assert r["error"]["code"] == -32063
    read = send("audit.read", {"filter": {"task_id": "tsk_absent"}})
    assert "error" not in read, read


def test_an_answer_option_that_is_an_object_is_refused_as_outside_the_set_and_recorded():
    _, send, ws = _ready(CHAINED + ["whisper/1.0"])
    tid = send("task.create", {"kind": "k", "input": {}, "assignee": "agent:b"},
               "agent:b")["result"]["task_id"]
    wid = send("whisper.ask", {"to": "human:c", "task_id": tid, "question": "?",
                               "deadline_ms": 60000, "default_if_lapsed": "a",
                               "options": [{"id": "a"}]}, "agent:b")["result"]["whisper_id"]
    before = len(ws.audit)
    r = send("whisper.answer", {"whisper_id": wid, "answer_option": {"x": 1}}, "human:c")
    assert r["error"]["code"] == -32022
    assert r["error"]["message"] == 'Answer option {"x":1} not in option set'
    assert len(ws.audit) == before + 1


# ------------------------------------------------------------ persistence

def test_refusals_survive_a_sqlite_restart(tmp_path):
    from chap_coordinator.storage.sqlite import SqliteStore
    path = tmp_path / "chap.db"
    _, send, ws = _ready(store=SqliteStore(str(path)))
    tid = _under_review(send)
    send("decide.approve", {"task_id": tid, "comment": "ok"}, "human:a")
    expected = [e.to_dict() for e in ws.audit]

    restarted = Coordinator(CoordinatorOptions(
        deterministic_ids=True, deterministic_clock=True, default_profiles=CHAINED,
        store=SqliteStore(str(path))))
    assert [e.to_dict() for e in restarted.get_workspace("w").audit] == expected
    v = restarted.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                            "params": {"workspace": "w", "from": "human:a"}})
    assert "error" not in v, v.get("error")
