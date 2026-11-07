"""
chap_coordinator.bridge: the CHAP half the five framework bridges share.

It reads a task through the coordinator's dispatch or a client, so it sees
a decision another process wrote to the store. It joins the agent before it
reads the workspace, so it works against a desk that requires signatures.
It joins no person without a key, and records no decision in a person's
name where people sign. A wait that ends without a decision withdraws its
review, so no later approval lands on work that never ran; and an async
wait leaves the event loop free, for a desk served on the same loop.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.bridge import (
    RECORDED_VIA, ChapError, Decision, PersonRequired, ReviewClient, ReviewRejected,
    artefact_digest, decided_arguments, is_decided, json_patch, wait_for_decision,
)
from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.client import wait_for_decision as client_wait_for_decision
from chap_coordinator.server import ChapDesk, load_config, make_coordinator, make_server
from chap_coordinator.storage.sqlite import SqliteStore

WS = "wsp_bridge"
AGENT = "agent:drafter#v1"
ALICE = "human:alice@example.org"
BOB = "human:bob@example.org"


def send(coord, method, sender, **params):
    """A call from someone other than the agent, as their own client makes it."""
    out = coord.dispatch({"jsonrpc": "2.0", "id": f"{method}-{time.monotonic_ns()}", "method": method,
                          "params": {"workspace": WS, "from": sender, **params}})
    assert "error" not in out, out
    return out["result"]


def join_with_a_key(coord, uri, keys):
    """A person joining from their own client, with their own key, as they
    must where signatures are required."""
    signer = Signer.load_or_create(uri, keys)
    out = coord.dispatch({"jsonrpc": "2.0", "id": f"join-{uri}", "method": "participant.join",
                          "params": {"workspace": WS, "from": uri, "type": "human",
                                     "jwks": {"keys": [signer.public_jwk]}}})
    assert "error" not in out, out


def entries(client, method):
    return [e["envelope"]["params"] for e in client.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


@pytest.fixture
def coord():
    return Coordinator(CoordinatorOptions(enable_chain=True))


@pytest.fixture
def agent(coord):
    return ReviewClient(coord, WS, AGENT, ALICE, name="test")


# -- what "decided" means ----------------------------------------------------

def test_a_task_under_review_or_paused_in_review_is_still_waiting():
    assert not is_decided({"state": "review_requested"})
    assert not is_decided({"state": "paused"})
    for state in ("completed", "declined", "in_progress", "abstained", "escalated", "cancelled"):
        assert is_decided({"state": state})


def test_the_clients_wait_reads_a_paused_review_as_waiting():
    states = iter(["review_requested", "paused", "paused", "completed"])

    class Desk:
        def task(self, task_id):
            return {"task_id": task_id, "state": next(states)}

    assert client_wait_for_decision(Desk(), "tsk_1", poll_seconds=0)["state"] == "completed"


def test_the_clients_wait_stops_at_its_timeout():
    class Desk:
        def task(self, task_id):
            return {"task_id": task_id, "state": "paused"}

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        client_wait_for_decision(Desk(), "tsk_1", poll_seconds=5, timeout=0.05)
    assert time.monotonic() - started < 1


def test_a_decision_reads_how_the_review_ended():
    def ending(state, kind=None):
        decisions = [{"kind": kind, "reviewer": ALICE}] if kind else []
        return Decision.from_view("t", {"state": state, "output": {"x": 1},
                                        "review": {"decisions": decisions}}).outcome

    assert ending("completed", "approve") == "approve"
    assert ending("completed", "override") == "override"
    assert ending("declined", "reject") == "reject"
    assert ending("in_progress", "reject") == "revise"
    assert ending("abstained", "abstain") == "abstain"
    assert ending("escalated") == "escalated"
    assert ending("in_progress") == "in_progress"
    assert Decision.from_view("t", {"state": "declined", "output": {"x": 1},
                                    "review": {"decisions": [{"kind": "reject"}]}}).output is None


def test_review_rejected_is_a_runtime_error_and_names_the_ending():
    rejected = ReviewRejected.from_decision(Decision("t", "in_progress", "reject", ALICE, comment="shorter"))
    assert isinstance(rejected, RuntimeError)
    assert (rejected.outcome, rejected.state, rejected.rationale, rejected.reviewer) == (
        "revise", "in_progress", "shorter", ALICE)


# -- the arguments a reviewed tool call runs with ----------------------------

CALL = {"tool": "transfer", "args": {"amount": 100, "to": "Alice"}, "tool_call_id": "c1"}


@pytest.mark.parametrize("output, runs", [
    (CALL, {"amount": 100, "to": "Alice"}),
    ({**CALL, "args": {"amount": 50, "to": "Alice"}}, {"amount": 50, "to": "Alice"}),
    ({"tool": "transfer", "tool_call_id": "c1"}, None),            # the arguments taken away
    ({**CALL, "args": None}, None),                                 # or made null
    ({**CALL, "args": [1]}, None),
    ({**CALL, "tool": "refund"}, None),                             # another tool
    ({**CALL, "tool_call_id": "c2"}, None),
    ({**CALL, "memo": "x"}, None),                                  # something the call does not carry
])
def test_an_approved_call_runs_only_as_decided(output, runs):
    approved = Decision("t", "completed", "override", ALICE, output=output)
    assert decided_arguments(approved, CALL) == runs


def test_a_call_nobody_approved_does_not_run():
    assert decided_arguments(Decision("t", "declined", "reject", ALICE), CALL) is None
    assert decided_arguments(Decision("t", "in_progress", "reject", ALICE), CALL) is None


def test_a_patch_turns_one_value_into_the_other():
    assert json_patch({"a": 1, "b": {"c": 2}, "d/e": 1}, {"a": 1, "b": {"c": 3}, "f": [1]}) == [
        {"op": "replace", "path": "/b/c", "value": 3},
        {"op": "remove", "path": "/d~1e"},
        {"op": "add", "path": "/f", "value": [1]},
    ]
    assert json_patch("x", "y") == [{"op": "replace", "path": "", "value": "y"}]
    assert json_patch({"a": 1}, {"a": 1}) == []


# -- joining ------------------------------------------------------------------

def test_the_agent_joins_before_anything_reads_the_workspace(coord):
    seen = []
    ReviewClient(coord, WS, AGENT, ALICE, on_envelope=lambda m, p: seen.append((m, p.get("from"))))
    assert seen == [("workspace.create", None), ("participant.join", AGENT)]
    assert set(coord.workspaces[WS].members) == {AGENT}


def test_a_workspace_that_exists_is_taken_as_it_is(coord):
    seen = []
    ReviewClient(coord, WS, "agent:other#v1", ALICE)
    ReviewClient(coord, WS, AGENT, ALICE, on_envelope=lambda m, p: seen.append((m, p.get("from"))))
    # The refusal to create it again is answered by a describe, made as the
    # agent once it has joined: never one from nobody.
    assert seen == [("workspace.create", None), ("participant.join", AGENT),
                    ("workspace.describe", AGENT)]


def test_a_workspace_without_review_is_refused(coord):
    coord.dispatch({"jsonrpc": "2.0", "id": "c", "method": "workspace.create",
                    "params": {"workspace": WS, "profiles": ["core/1.0"]}})
    with pytest.raises(ValueError, match="does not advertise review/1.0"):
        ReviewClient(coord, WS, AGENT, ALICE)


def test_the_participants_and_the_agent_must_agree(coord):
    with pytest.raises(ValueError, match="'human:' URI"):
        ReviewClient(coord, WS, AGENT, "agent:reviewer")
    with pytest.raises(ValueError, match="'agent:' or 'service:'"):
        ReviewClient(coord, WS, ALICE, ALICE)
    with pytest.raises(ValueError, match="calls as agent:someone"):
        ReviewClient(Participant(coord, WS, "agent:someone"), WS, AGENT, ALICE)


# -- request and wait ---------------------------------------------------------

def test_a_review_opens_on_a_task_that_requires_one_and_a_desk_decision_is_read_back(coord, agent):
    task_id = agent.open({"reply": "refund 100"}, kind="draft")
    task = coord.workspaces[WS].tasks[task_id]
    assert (task.state, task.review_required, task.review.requested_to) == ("review_requested", True, [ALICE])
    assert (task.input, task.pending_artefact) == ({}, {"reply": "refund 100"})
    assert [p.get("to") for p in entries(agent, "review.request")] == []
    threading.Timer(0.05, send, (coord, "decide.override", ALICE), {
        "task_id": task_id, "rationale": "cap", "tags": ["limit"],
        "diff": [{"op": "replace", "path": "/reply", "value": "refund 50"}]}).start()
    decision = agent.wait(task_id, timeout=5, poll_seconds=0.01)
    assert decision == Decision(task_id, "completed", "override", ALICE, {"reply": "refund 50"}, "cap", ("limit",))


def test_an_empty_artefact_is_not_put_in_front_of_anyone(agent):
    for empty in (None, "", "  ", {}, []):
        with pytest.raises(ValueError, match="nothing to review"):
            agent.open(empty, kind="draft")


def test_a_wait_that_times_out_withdraws_the_review(coord, agent):
    task_id = agent.open({"reply": "refund 100"}, kind="draft")
    with pytest.raises(TimeoutError, match="withdrawn"):
        agent.wait(task_id, timeout=0.05, poll_seconds=0.01)
    task = coord.workspaces[WS].tasks[task_id]
    assert task.state == "in_progress"
    assert "stopped waiting" in task.history[-1].note
    # A decision made after the agent stopped waiting is refused: none is
    # recorded for work nobody carries out.
    late = coord.dispatch({"jsonrpc": "2.0", "id": "late", "method": "decide.approve",
                           "params": {"workspace": WS, "from": ALICE, "task_id": task_id}})
    assert late["error"]["code"] == -32010
    # The next proposal opens the review's next round on the same task.
    assert agent.open({"reply": "refund 90"}, kind="draft", task_id=task_id) == task_id
    assert coord.workspaces[WS].tasks[task_id].state == "review_requested"


def test_a_decision_that_lands_as_the_wait_times_out_is_kept(coord, agent):
    task_id = agent.open({"reply": "refund 100"}, kind="draft")
    reads = []
    real = agent.read

    def read(tid):
        reads.append(tid)
        if len(reads) == 2:   # the read the withdrawal makes first
            send(coord, "decide.approve", ALICE, task_id=tid)
        return real(tid)

    agent.read = read
    decision = agent.wait(task_id, timeout=0.01, poll_seconds=0.05)
    assert (decision.kind, decision.state) == ("approve", "completed")


def test_a_wait_cancelled_in_async_code_withdraws_the_review_and_lets_the_loop_end(coord, agent):
    async def main():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(agent.review_async({"reply": "refund 100"}, kind="draft",
                                                      poll_seconds=0.02), 0.2)

    started = time.monotonic()
    asyncio.run(main())
    assert time.monotonic() - started < 2
    [task] = coord.workspaces[WS].tasks.values()
    assert task.state == "in_progress"


def test_a_review_whose_opening_is_cancelled_is_withdrawn_once_open(coord, agent):
    gate = threading.Event()
    submit = agent._submit

    def slow_submit(*args):
        gate.wait(5)
        return submit(*args)

    agent._submit = slow_submit

    async def main():
        opening = asyncio.ensure_future(agent.open_async({"reply": "refund 100"}, kind="draft",
                                                         poll_seconds=0.01))
        await asyncio.sleep(0.1)
        opening.cancel()
        await asyncio.sleep(0.05)
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await opening

    asyncio.run(main())
    [task] = coord.workspaces[WS].tasks.values()
    assert task.state == "in_progress"


def test_a_signed_workspace_opens_a_review_once_a_person_has_joined(tmp_path):
    pytest.importorskip("cryptography")
    signed = Coordinator(CoordinatorOptions(enable_chain=True, require_signatures=True,
                                            signature_policy="humans"))
    agent = ReviewClient(signed, WS, AGENT, ALICE)
    with pytest.raises(TimeoutError, match="Nobody joined"):
        agent.open({"reply": "refund 100"}, kind="draft", timeout=0.05, poll_seconds=0.01)
    # Nothing was written, and nobody was joined in the person's name.
    assert signed.workspaces[WS].tasks == {} and ALICE not in signed.workspaces[WS].members
    threading.Timer(0.05, join_with_a_key, (signed, BOB, tmp_path / "keys")).start()
    task_id = agent.open({"reply": "refund 100"}, kind="draft", timeout=5, poll_seconds=0.01)
    assert signed.workspaces[WS].tasks[task_id].review.requested_to == [BOB]


def test_a_store_written_by_another_process_is_read(tmp_path):
    path = tmp_path / "chap.sqlite"
    here = Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True))
    agent = ReviewClient(here, WS, AGENT, ALICE)
    task_id = agent.open({"reply": "refund 100"}, kind="draft")
    # The desk is another process with its own coordinator over the same file.
    desk = Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True))
    send(desk, "decide.approve", ALICE, task_id=task_id, comment="ok")
    decision = agent.wait(task_id, timeout=2, poll_seconds=0.01)
    assert (decision.kind, decision.comment, decision.output) == ("approve", "ok", {"reply": "refund 100"})


def test_a_call_whose_write_was_lost_is_sent_once_more(coord):
    answers = iter([{"jsonrpc": "2.0", "id": "x", "error": {"code": -32603, "message": "Internal error",
                                                             "data": {"reason": "store"}}}])

    class Flaky:
        def dispatch(self, envelope):
            if envelope["method"] == "task.create":
                answer = next(answers, None)
                if answer is not None:
                    return answer
            return coord.dispatch(envelope)

    agent = ReviewClient(Flaky(), WS, AGENT, ALICE, client=type("R", (), {"task": lambda s, t: None})())
    agent.call("participant.join", {"type": "human"}, sender=ALICE)
    task_id = agent.call("task.create", {"assignee": AGENT, "kind": "draft", "input": {}})["task_id"]
    assert list(coord.workspaces[WS].tasks) == [task_id]


def test_a_refusal_is_a_chap_error_naming_the_method(agent):
    with pytest.raises(ChapError) as refused:
        agent.call("decide.approve", {"task_id": "tsk_none"}, sender=ALICE)
    assert refused.value.method == "decide.approve" and refused.value.code == -32011


# -- recording a decision made elsewhere --------------------------------------

def test_a_recorded_decision_carries_the_digest_shown_and_is_marked_as_recorded(coord, agent):
    decision = agent.record({"reply": "refund 100"}, "approve", task_kind="draft", comment="fine")
    assert (decision.kind, decision.output) == ("approve", {"reply": "refund 100"})
    [params] = entries(agent, "decide.approve")
    # The coordinator keeps a param it does not know on the log unchanged.
    assert params["via"] == RECORDED_VIA == "bridge-record"
    assert params["approved_artefact_digest"] == artefact_digest({"reply": "refund 100"})


def test_a_recorded_override_and_rejection(coord, agent):
    artefact = {"reply": "refund 100"}
    edited = agent.record(artefact, "override", task_kind="draft", comment="cap",
                          diff=json_patch(artefact, {"reply": "refund 50"}))
    assert (edited.kind, edited.output, edited.comment) == ("override", {"reply": "refund 50"}, "cap")
    sent_back = agent.record(artefact, "reject", task_kind="draft", reviewer=BOB, comment="no")
    assert (sent_back.outcome, sent_back.reviewer) == ("reject", BOB)
    assert [p["via"] for p in entries(agent, "decide.override") + entries(agent, "decide.reject")] == [
        "bridge-record", "bridge-record"]


def test_a_decision_recorded_again_is_read_back_and_not_recorded_twice(coord, agent):
    artefact = {"reply": "refund 100"}
    task_id = agent.open(artefact, kind="draft")
    first = agent.decide(task_id, "reject", digest=artefact_digest(artefact), comment="no")
    again = agent.decide(task_id, "reject", digest=artefact_digest(artefact), comment="no")
    assert first == again and first.outcome == "reject"
    assert [e["envelope"]["method"] for e in agent.audit()][-1] == "decide.reject"
    # Someone else's decision, or another kind, is not taken for it.
    with pytest.raises(ChapError, match="not awaiting review"):
        agent.decide(task_id, "approve", digest=artefact_digest(artefact))
    with pytest.raises(ChapError, match="not awaiting review"):
        agent.decide(task_id, "reject", digest=artefact_digest(artefact), reviewer=BOB)


def test_a_decision_on_another_artefact_than_the_one_shown_is_refused(agent):
    task_id = agent.open({"reply": "refund 100"}, kind="draft")
    with pytest.raises(ChapError) as refused:
        agent.decide(task_id, "approve", digest=artefact_digest({"reply": "refund 1000"}))
    assert refused.value.code == -32074


def test_nobody_is_recorded_or_joined_in_a_persons_name_where_signatures_are_required(tmp_path):
    pytest.importorskip("cryptography")
    signed = Coordinator(CoordinatorOptions(enable_chain=True, require_signatures=True,
                                            signature_policy="humans"))
    agent = ReviewClient(signed, WS, AGENT, ALICE)
    with pytest.raises(PersonRequired, match="requires signatures"):
        agent.record({"reply": "refund 100"}, "approve", task_kind="draft")
    join_with_a_key(signed, BOB, tmp_path / "keys")
    task_id = agent.open({"reply": "refund 100"}, kind="draft", timeout=5)
    with pytest.raises(PersonRequired):
        agent.decide(task_id, "approve", digest=artefact_digest({"reply": "refund 100"}))
    assert set(signed.workspaces[WS].members) == {AGENT, BOB}
    assert signed.workspaces[WS].tasks[task_id].state == "review_requested"


# -- over HTTP, against a desk that requires signatures ------------------------

def desk_config(tmp_path, monkeypatch, signatures):
    """A desk's configuration as chap-coordinator serve reads it."""
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "chap.config.json"
    path.write_text(json.dumps({
        "workspace": WS, "profiles": ["core/1.0", "review/1.0"], "store": ":memory:", "port": 0,
        "require_signatures": signatures, "chain": True,
        "humans": [{"uri": ALICE, "display_name": "Alice", "role": "reviewer"}],
        "agent": {"uri": AGENT},
    }), encoding="utf-8")
    return load_config(path)


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """A desk server as chap-coordinator serve makes one, people signing."""
    config = desk_config(tmp_path, monkeypatch, "humans")
    server = make_server(config, make_coordinator(config))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield HttpCoordinator(f"http://127.0.0.1:{server.server_port}/chap")
    finally:
        server.shutdown()
        server.server_close()


def test_request_and_wait_against_a_desk_where_people_sign(desk, tmp_path):
    pytest.importorskip("cryptography")
    alice = Participant(desk, WS, ALICE, Signer.load_or_create(ALICE, tmp_path / "keys"))
    alice.join("human", "reviewer")
    for coord in (desk, Participant(desk, WS, AGENT)):
        agent = ReviewClient(coord, WS, AGENT, ALICE)
        assert agent.requires_signatures()
        task_id = agent.open({"reply": "refund 100"}, kind="draft", timeout=5, poll_seconds=0.05)
        threading.Timer(0.1, alice.call, ("decide.override",), {
            "task_id": task_id, "rationale": "cap",
            "diff": [{"op": "replace", "path": "/reply", "value": "refund 50"}]}).start()
        decision = agent.wait(task_id, timeout=5, poll_seconds=0.05)
        assert (decision.kind, decision.reviewer, decision.output) == ("override", ALICE, {"reply": "refund 50"})
        with pytest.raises(PersonRequired):
            agent.record({"reply": "refund 7"}, "approve", task_kind="draft")
    # Alice's own name is still hers: the bridge joined nobody in it.
    assert alice.call("participant.join", type="human", role="reviewer",
                      jwks={"keys": [alice.signer.public_jwk]}) == {"joined": True, "as": ALICE}


def test_an_async_wait_leaves_the_loop_free_for_a_desk_served_on_it(tmp_path, monkeypatch):
    """The desk and the agent on one event loop, as in one ASGI app: each
    read the agent makes over HTTP is answered by that loop, and so is the
    person's decision."""
    uvicorn = pytest.importorskip("uvicorn")
    config = desk_config(tmp_path, monkeypatch, False)
    server = uvicorn.Server(uvicorn.Config(ChapDesk(config, make_coordinator(config)), host="127.0.0.1",
                                           port=0, log_level="warning", lifespan="off"))

    async def main():
        serving = asyncio.ensure_future(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        url = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}/chap"
        try:
            agent = await asyncio.to_thread(ReviewClient, HttpCoordinator(url, timeout=3), WS, AGENT, ALICE)
            task_id = await agent.open_async({"reply": "refund 100"}, kind="draft", timeout=5, poll_seconds=0.05)
            alice = Participant(HttpCoordinator(url, timeout=3), WS, ALICE)
            deciding = asyncio.ensure_future(asyncio.to_thread(alice.call, "decide.approve", task_id=task_id))
            decision = await agent.wait_async(task_id, timeout=5, poll_seconds=0.05)
            await deciding
            return decision
        finally:
            server.should_exit = True
            await serving

    decision = asyncio.run(main())
    assert (decision.outcome, decision.reviewer, decision.output) == ("approve", ALICE, {"reply": "refund 100"})
