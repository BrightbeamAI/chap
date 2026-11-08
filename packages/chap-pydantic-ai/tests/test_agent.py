"""
chap-pydantic-ai with a real Pydantic AI agent: TestModel proposes calls
that need approval, the bridge opens a review of each and waits, a person
decides at a desk that shares the coordinator, and the agent's next run
carries the decision out.

Skipped where Pydantic AI is not installed; scripts/test-bridges.sh
installs it, and fails where they are skipped.
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

pytest.importorskip("pydantic_ai")

from pydantic_ai import Agent, DeferredToolRequests, RunContext, ToolApproved, ToolDenied
from pydantic_ai.models.test import TestModel

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_pydantic_ai import ChapApprovalBridge

WS = "wsp_pay"
AGENT = "agent:assistant#v1"
REVIEWER = "human:alice@example.org"


class Desk(threading.Thread):
    """A person at a desk: they decide each review as it opens, in their
    own name, through ``send`` (the coordinator's dispatch, or their own
    client's). ``decide(artefact)`` gives the method and params, or None to
    leave the review alone."""

    def __init__(self, send, decide, reviews=1, delay=0.0):
        super().__init__(daemon=True)
        self.send, self.decide, self.reviews, self.delay = send, decide, reviews, delay
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
                if submitted["task_id"] in seen:
                    continue
                seen.add(submitted["task_id"])
                time.sleep(self.delay)
                decided = self.decide(submitted["output"])
                if decided is not None:
                    method, params = decided
                    self.answers.append(self.call(method, task_id=submitted["task_id"], **params))
            time.sleep(0.01)


@pytest.fixture
def setup():
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapApprovalBridge(coord, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    agent = Agent(TestModel(), output_type=[str, DeferredToolRequests])
    executed = []

    @agent.tool(requires_approval=True)
    def transfer(ctx: RunContext, amount: int, to: str) -> str:
        executed.append({"amount": amount, "to": to, "metadata": ctx.tool_call_metadata})
        return f"sent {amount} to {to}"

    return coord, bridge, agent, executed


def decide_and_resume(coord, bridge, agent, method, **params):
    first = agent.run_sync("pay the invoice")
    assert isinstance(first.output, DeferredToolRequests)
    desk = Desk(coord.dispatch, lambda artefact: (method, params))
    desk.start()
    results = bridge.request_results(first.output, timeout=5, poll_seconds=0.01)
    desk.join()
    assert all("error" not in answer for answer in desk.answers), desk.answers
    final = agent.run_sync("pay the invoice", message_history=first.all_messages(), deferred_tool_results=results)
    return first.output.approvals[0], results, final


def calls(bridge, method):
    return [e["envelope"]["params"] for e in bridge.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


def task(coord, task_id):
    return coord.workspaces[WS].tasks[task_id]


def test_an_approval_runs_the_call_as_proposed(setup):
    coord, bridge, agent, executed = setup
    call, results, _ = decide_and_resume(coord, bridge, agent, "decide.approve")
    approved = results.approvals[call.tool_call_id]
    assert isinstance(approved, ToolApproved) and approved.override_args is None
    task_id = results.metadata[call.tool_call_id]["chap_task_id"]
    assert executed == [{**call.args_as_dict(), "metadata": {
        "chap_task_id": task_id, "reviewer": REVIEWER, "outcome": "approve"}}]
    # The person decided; the bridge recorded no decision of its own.
    assert [(p["from"], p.get("via")) for p in calls(bridge, "decide.approve")] == [(REVIEWER, None)]
    assert task(coord, task_id).review_required is True


def test_an_override_runs_the_call_with_the_reviewers_arguments(setup):
    coord, bridge, agent, executed = setup
    call, results, _ = decide_and_resume(coord, bridge, agent, "decide.override", rationale="over the limit",
                                         diff=[{"op": "replace", "path": "/args/amount", "value": 50}])
    assert results.approvals[call.tool_call_id].override_args == {**call.args_as_dict(), "amount": 50}
    assert [{k: v for k, v in run.items() if k != "metadata"} for run in executed] == [
        {**call.args_as_dict(), "amount": 50}]


def test_a_rejection_denies_the_call_with_the_reviewers_note(setup):
    coord, bridge, agent, executed = setup
    call, results, final = decide_and_resume(coord, bridge, agent, "decide.reject", comment="not on the allow-list")
    denied = results.approvals[call.tool_call_id]
    assert isinstance(denied, ToolDenied) and denied.message == "not on the allow-list"
    assert executed == [] and "not on the allow-list" in final.output


@pytest.mark.parametrize("diff", [
    [{"op": "replace", "path": "/tool", "value": "refund"}],     # another tool
    [{"op": "remove", "path": "/args"}],                         # the arguments taken away
    [{"op": "replace", "path": "/args", "value": None}],         # or made null
])
def test_an_approved_edit_the_run_cannot_carry_out_runs_nothing(setup, diff):
    coord, bridge, agent, executed = setup
    call, results, _ = decide_and_resume(coord, bridge, agent, "decide.override", diff=diff, rationale="edit")
    assert isinstance(results.approvals[call.tool_call_id], ToolDenied)
    assert executed == []


def test_an_override_the_tool_cannot_take_runs_nothing(setup):
    """Pydantic AI checks an override against the tool's arguments, and
    tells the model where it does not fit; the tool is not called."""
    coord, bridge, agent, executed = setup
    decide_and_resume(coord, bridge, agent, "decide.override", rationale="cap",
                      diff=[{"op": "replace", "path": "/args/amount", "value": "fifty"}])
    assert executed == []


def test_a_revision_denies_the_call_says_so_and_closes_its_task(setup):
    coord, bridge, agent, executed = setup
    call, results, _ = decide_and_resume(coord, bridge, agent, "decide.reject", comment="split it in two",
                                         request_revision=True)
    assert results.approvals[call.tool_call_id].message == "split it in two"
    meta = results.metadata[call.tool_call_id]
    assert meta["outcome"] == "revise"
    closed = task(coord, meta["chap_task_id"])
    assert closed.state == "declined" and "reviewed on a task of its own" in closed.history[-1].note
    assert executed == []


def test_a_call_nobody_decides_in_time_is_denied_and_its_review_withdrawn(setup):
    coord, bridge, agent, executed = setup
    first = agent.run_sync("pay the invoice")
    results = bridge.request_results(first.output, timeout=0.1, poll_seconds=0.02)
    [call] = first.output.approvals
    assert "Nobody decided" in results.approvals[call.tool_call_id].message
    meta = results.metadata[call.tool_call_id]
    assert meta["outcome"] == "no decision"
    # A decision made after the agent stopped waiting is refused: none lands
    # on a call that never ran.
    late = coord.dispatch({"jsonrpc": "2.0", "id": "late", "method": "decide.approve",
                           "params": {"workspace": WS, "from": REVIEWER, "task_id": meta["chap_task_id"]}})
    assert late["error"]["code"] == -32010
    agent.run_sync("pay the invoice", message_history=first.all_messages(), deferred_tool_results=results)
    assert executed == []
    assert [t.state for t in coord.workspaces[WS].tasks.values()] == ["in_progress"]


def test_a_decision_made_in_time_is_kept_when_another_call_is_not_decided(setup):
    coord, bridge, agent, executed = setup

    @agent.tool(requires_approval=True)
    def refund(ctx: RunContext, amount: int) -> str:
        executed.append({"refund": amount})
        return f"refunded {amount}"

    first = agent.run_sync("pay and refund")
    assert len(first.output.approvals) == 2
    desk = Desk(coord.dispatch, lambda artefact: ("decide.approve", {}) if artefact["tool"] == "transfer" else None,
                reviews=2)
    desk.start()
    results = bridge.request_results(first.output, timeout=0.5, poll_seconds=0.02)
    desk.join()
    by_tool = {c.tool_name: results.approvals[c.tool_call_id] for c in first.output.approvals}
    assert isinstance(by_tool["transfer"], ToolApproved) and isinstance(by_tool["refund"], ToolDenied)
    agent.run_sync("pay and refund", message_history=first.all_messages(), deferred_tool_results=results)
    assert [run.get("to") or run for run in executed] == ["a"]


def test_async_code_awaits_the_decision_and_a_cancelled_wait_withdraws_the_review(setup):
    coord, bridge, agent, executed = setup

    async def main():
        first = await agent.run("pay the invoice")
        desk = Desk(coord.dispatch, lambda artefact: ("decide.approve", {}))
        desk.start()
        results = await bridge.request_results_async(first.output, timeout=5, poll_seconds=0.01)
        desk.join()
        await agent.run("pay the invoice", message_history=first.all_messages(), deferred_tool_results=results)
        second = await agent.run("pay again")
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bridge.request_results_async(second.output, poll_seconds=0.05), 0.2)

    started = time.monotonic()
    asyncio.run(main())
    assert time.monotonic() - started < 3
    assert len(executed) == 1
    assert [t.state for t in coord.workspaces[WS].tasks.values()] == ["completed", "in_progress"]


def test_a_decision_written_by_another_process_is_read(tmp_path):
    from chap_coordinator.storage.sqlite import SqliteStore

    path = tmp_path / "chap.sqlite"
    here = Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True))
    bridge = ChapApprovalBridge(here, workspace=WS, agent=AGENT, reviewer=REVIEWER)
    agent = Agent(TestModel(), output_type=[str, DeferredToolRequests])

    @agent.tool_plain(requires_approval=True)
    def transfer(amount: int, to: str) -> str:
        return f"sent {amount} to {to}"

    first = agent.run_sync("pay")
    desk = Desk(Coordinator(CoordinatorOptions(store=SqliteStore(path), enable_chain=True)).dispatch,
                lambda artefact: ("decide.approve", {}), delay=0.05)
    desk.start()
    results = bridge.request_results(first.output, timeout=5, poll_seconds=0.01)
    desk.join()
    assert isinstance(results.approvals[first.output.approvals[0].tool_call_id], ToolApproved)


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
        bridge = ChapApprovalBridge(Participant(http, WS, AGENT), workspace=WS, agent=AGENT, reviewer=REVIEWER)
        agent = Agent(TestModel(), output_type=[str, DeferredToolRequests])
        executed = []

        @agent.tool_plain(requires_approval=True)
        def transfer(amount: int, to: str) -> str:
            executed.append(amount)
            return f"sent {amount} to {to}"

        first = agent.run_sync("pay")
        desk = Desk(lambda envelope: http.dispatch(alice.signer.sign(envelope)),
                    lambda artefact: ("decide.override", {
                        "rationale": "cap", "diff": [{"op": "replace", "path": "/args/amount", "value": 7}]}))
        desk.start()
        results = bridge.request_results(first.output, timeout=5, poll_seconds=0.02)
        desk.join()
        agent.run_sync("pay", message_history=first.all_messages(), deferred_tool_results=results)
        assert executed == [7]
        [override] = [e["envelope"] for e in alice.call("audit.read")["entries"]
                      if "envelope" in e and e["envelope"]["method"] == "decide.override"]
        assert override["params"]["from"] == REVIEWER and override["sig"].startswith("ed25519:")
    finally:
        server.shutdown()
        server.server_close()
