"""
chap-llama-index with a real LlamaIndex Workflow: a step proposes a payment
and pauses on an InputRequiredEvent, the bridge opens a review and waits, a
person decides at a desk, and the HumanResponseEvent the bridge returns
resumes the workflow.

Skipped where llama-index-workflows is not installed;
scripts/test-bridges.sh installs it, and fails where they are skipped.
"""

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

pytest.importorskip("workflows")

from workflows import Context, Workflow, step
from workflows.events import HumanResponseEvent, InputRequiredEvent, StartEvent, StopEvent

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_llama_index import ChapHitlBridge

WS = "wsp_li"
AGENT = "agent:writer#v1"
REVIEWER = "human:alice@example.org"
PROPOSED = {"amount": 100, "to": "acct-9"}


class Desk(threading.Thread):
    """A person at a desk: they decide each review as it opens, in their own
    name, through ``send``. ``decide(artefact)`` gives the method and
    params, or None to leave the review alone."""

    def __init__(self, send, decide, reviews=1):
        super().__init__(daemon=True)
        self.send, self.decide, self.reviews = send, decide, reviews
        self.answers = []

    def call(self, method, **params):
        return self.send({"jsonrpc": "2.0", "id": f"desk-{time.monotonic_ns()}", "method": method,
                          "params": {"workspace": WS, "from": REVIEWER, **params}})

    def run(self):
        seen = set()
        deadline = time.monotonic() + 10
        while len(seen) < self.reviews and time.monotonic() < deadline:
            for entry in self.call("audit.read", filter={"method": "task.complete"})["result"]["entries"]:
                submitted = entry["envelope"]["params"]
                key = (submitted["task_id"], entry["seq"])
                if key in seen:
                    continue
                seen.add(key)
                decided = self.decide(submitted["output"])
                if decided is not None:
                    method, params = decided
                    self.answers.append(self.call(method, task_id=submitted["task_id"], **params))
            time.sleep(0.01)


class PaymentReview(Workflow):
    """Propose a payment; pay it as decided; propose a smaller one when sent
    back for another."""

    def __init__(self, paid: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self.paid = paid

    @step
    async def propose(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
        return InputRequiredEvent(prefix="approve payment? ", proposed=PROPOSED)

    @step
    async def pay(self, ctx: Context, ev: HumanResponseEvent) -> InputRequiredEvent | StopEvent:
        if ev.get("decision") == "revise":
            return InputRequiredEvent(prefix="approve payment? ", proposed={**PROPOSED, "amount": 40},
                                      chap_task_id=ev.get("chap_task_id"))
        if ev.get("decision") in ("approve", "override"):
            self.paid.append(ev.get("response"))
            return StopEvent(result=f"paid {ev.get('response')}")
        return StopEvent(result=f"not paid ({ev.get('decision')}): {ev.get('response')}")


def make():
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    return coord, ChapHitlBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)


async def drive(bridge, workflow, **wait):
    handler = workflow.run()
    async for event in handler.stream_events():
        if isinstance(event, InputRequiredEvent):
            handler.ctx.send_event(await bridge.request_response(
                event, task_id=event.get("chap_task_id"), poll_seconds=0.01, **wait))
    return await handler


def run(decide, reviews=1, **wait):
    coord, bridge = make()
    paid: list = []
    desk = Desk(coord.dispatch, decide, reviews)
    desk.start()
    result = asyncio.run(drive(bridge, PaymentReview(paid, timeout=None), timeout=5, **wait))
    desk.join()
    assert all("error" not in answer for answer in desk.answers), desk.answers
    return coord, bridge, paid, result


def test_an_approval_resumes_the_workflow_with_the_proposed_output():
    coord, bridge, paid, result = run(lambda artefact: ("decide.approve", {"comment": "ok"}))
    assert paid == [PROPOSED] and result == f"paid {PROPOSED}"
    [task] = coord.workspaces[WS].tasks.values()
    assert (task.state, task.review_required) == ("completed", True)


def test_an_override_resumes_it_with_the_reviewers_version():
    _, _, paid, _ = run(lambda artefact: ("decide.override", {
        "rationale": "cap", "diff": [{"op": "replace", "path": "/amount", "value": 50}]}))
    assert paid == [{"amount": 50, "to": "acct-9"}]


def test_a_rejection_pays_nothing_and_says_why():
    _, _, paid, result = run(lambda artefact: ("decide.reject", {"comment": "too much"}))
    assert paid == [] and result == "not paid (reject): too much"


def test_an_abstention_reads_as_one():
    _, _, paid, result = run(lambda artefact: ("abstain.declare", {"reason": "not mine"}))
    assert paid == [] and result == "not paid (abstain): not mine"


def test_a_revision_opens_the_next_round_of_the_same_review():
    decisions = iter([("decide.reject", {"comment": "smaller", "request_revision": True}),
                      ("decide.approve", {})])
    coord, bridge, paid, _ = run(lambda artefact: next(decisions), reviews=2)
    assert paid == [{"amount": 40, "to": "acct-9"}]
    [task] = coord.workspaces[WS].tasks.values()
    assert task.state == "completed"
    completions = [e for e in bridge.audit() if e.get("envelope", {}).get("method") == "task.complete"]
    assert len(completions) == 2


class Routed(Workflow):
    """Two payments, each waiting for its own response, routed by request_id."""

    @step
    async def start(self, ctx: Context, ev: StartEvent) -> StopEvent:
        out = []
        for rid, amount in (("p1", 10), ("p2", 10000)):
            response = await ctx.wait_for_event(
                HumanResponseEvent,
                waiter_event=InputRequiredEvent(prefix="approve?", request_id=rid,
                                                proposed={"request_id": rid, "amount": amount}),
                waiter_id=rid, requirements={"request_id": rid}, timeout=5)
            out.append((rid, response.get("decision")))
        return StopEvent(result=out)


def test_a_response_carries_the_fields_a_waiting_step_routes_on():
    coord, bridge = make()
    desk = Desk(coord.dispatch, lambda artefact: (
        ("decide.approve", {}) if artefact["amount"] < 100 else ("decide.reject", {"comment": "no"})), reviews=2)
    desk.start()

    async def main():
        handler = Routed(timeout=None).run()
        async for event in handler.stream_events():
            if isinstance(event, InputRequiredEvent):
                response = await bridge.request_response(event, timeout=5, poll_seconds=0.01)
                assert (response.get("request_id"), response.get("prefix")) == (event.get("request_id"), "approve?")
                handler.ctx.send_event(response)
        return await handler

    assert asyncio.run(main()) == [("p1", "approve"), ("p2", "reject")]
    desk.join()


def test_a_wait_that_times_out_withdraws_the_review():
    coord, bridge = make()

    async def main():
        handler = PaymentReview([], timeout=None).run()
        async for event in handler.stream_events():
            if isinstance(event, InputRequiredEvent):
                with pytest.raises(TimeoutError, match="withdrawn"):
                    await bridge.request_response(event, timeout=0.1, poll_seconds=0.02)
                await handler.cancel_run()

    asyncio.run(main())
    [task] = coord.workspaces[WS].tasks.values()
    assert task.state == "in_progress"
    late = coord.dispatch({"jsonrpc": "2.0", "id": "late", "method": "decide.approve",
                           "params": {"workspace": WS, "from": REVIEWER, "task_id": task.id}})
    assert late["error"]["code"] == -32010


def test_a_cancelled_wait_withdraws_the_review_and_lets_the_loop_end():
    coord, bridge = make()

    async def main():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bridge.request_response(InputRequiredEvent(proposed=PROPOSED),
                                                           poll_seconds=0.05), 0.2)

    started = time.monotonic()
    asyncio.run(main())
    assert time.monotonic() - started < 2
    assert [t.state for t in coord.workspaces[WS].tasks.values()] == ["in_progress"]


def test_a_decision_written_by_another_process_is_read(tmp_path):
    from chap_coordinator.storage.sqlite import SqliteStore

    path = tmp_path / "chap.sqlite"
    bridge = ChapHitlBridge(Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True)),
                            workspace=WS, agent=AGENT, reviewer=REVIEWER)
    desk = Desk(Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True)).dispatch,
                lambda artefact: ("decide.approve", {}))
    desk.start()
    response = asyncio.run(bridge.request_response(InputRequiredEvent(proposed=PROPOSED), timeout=5,
                                                   poll_seconds=0.01))
    desk.join()
    assert (response.get("decision"), response.get("response")) == ("approve", PROPOSED)


def test_request_and_wait_against_a_desk_where_people_sign(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    # A person's first join takes the code the desk server prints at start.
    monkeypatch.setenv("CHAP_ENROLMENT_CODE", "TESTS-ENROL")
    path = tmp_path / "chap.config.json"
    path.write_text(json.dumps({
        "workspace": WS, "profiles": ["core/1.0", "review/1.0"], "store": ":memory:", "port": 0,
        "require_signatures": "humans", "chain": True,
        "humans": [{"uri": REVIEWER, "display_name": "Alice", "role": "reviewer"}], "agent": {"uri": AGENT},
    }), encoding="utf-8")
    config = load_config(path)
    server = make_server(config, make_coordinator(config))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        http = HttpCoordinator(f"http://127.0.0.1:{server.server_port}/chap")
        alice = Participant(http, WS, REVIEWER, Signer.load_or_create(REVIEWER, tmp_path / "keys"),
                            enrolment="TESTS-ENROL")
        alice.join("human", "reviewer")
        bridge = ChapHitlBridge(Participant(http, WS, AGENT), workspace=WS, agent=AGENT, reviewer=REVIEWER)
        desk = Desk(lambda envelope: http.dispatch(alice.signer.sign(envelope)), lambda artefact: (
            "decide.override", {"rationale": "cap", "diff": [{"op": "replace", "path": "/amount", "value": 7}]}))
        desk.start()
        paid: list = []
        assert asyncio.run(drive(bridge, PaymentReview(paid, timeout=None), timeout=5)) == \
            "paid {'amount': 7, 'to': 'acct-9'}"
        desk.join()
    finally:
        server.shutdown()
        server.server_close()
