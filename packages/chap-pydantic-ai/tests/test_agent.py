"""
chap-pydantic-ai with a real Pydantic AI agent: TestModel proposes a
`transfer` call that requires approval, the bridge opens a review and
waits, a person decides at a desk that shares the coordinator, and the
agent's next run carries the decision out.

Skipped where Pydantic AI is not installed; scripts/test-bridges.sh
installs it.
"""

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
from chap_pydantic_ai import ChapApprovalBridge


REVIEWER = "human:alice@example.org"


class Desk(threading.Thread):
    """A person at a desk sharing the coordinator: they join, wait for a
    review addressed to them, and decide it."""

    def __init__(self, coord, method, **params):
        super().__init__(daemon=True)
        self.coord, self.method, self.params = coord, method, params
        self.decided = None

    def run(self):
        self.coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                             "params": {"workspace": "wsp_pay", "from": REVIEWER,
                                        "type": "human"}})
        deadline = time.monotonic() + 5
        while self.decided is None and time.monotonic() < deadline:
            ws = self.coord.workspaces["wsp_pay"]
            for task in list(ws.tasks.values()):
                if task.state == "review_requested" and REVIEWER in task.review.requested_to:
                    self.decided = self.coord.dispatch({
                        "jsonrpc": "2.0", "id": "d", "method": self.method,
                        "params": {"workspace": "wsp_pay", "from": REVIEWER,
                                   "task_id": task.id, **self.params}})
                    break
            time.sleep(0.01)


@pytest.fixture
def setup():
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapApprovalBridge(coord, workspace="wsp_pay", agent="agent:assistant#v1",
                                reviewer=REVIEWER)
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
    desk = Desk(coord, method, **params)
    desk.start()
    results = bridge.request_results(first.output, timeout=5, poll_seconds=0.01)
    desk.join()
    assert "error" not in desk.decided, desk.decided
    final = agent.run_sync("pay the invoice", message_history=first.all_messages(),
                           deferred_tool_results=results)
    return first.output.approvals[0], results, final


def calls(bridge, method):
    return [e["envelope"]["params"] for e in bridge.audit()
            if "envelope" in e and e["envelope"]["method"] == method]


def test_an_approval_runs_the_call_as_proposed(setup):
    coord, bridge, agent, executed = setup
    call, results, _ = decide_and_resume(coord, bridge, agent, "decide.approve")
    assert isinstance(results.approvals[call.tool_call_id], ToolApproved)
    assert results.approvals[call.tool_call_id].override_args is None
    task_id = results.metadata[call.tool_call_id]["chap_task_id"]
    assert executed == [{**call.args_as_dict(), "metadata": {
        "chap_task_id": task_id, "reviewer": REVIEWER}}]
    # The person decided; the bridge recorded no decision of its own.
    assert [p["from"] for p in calls(bridge, "decide.approve")] == [REVIEWER]
    assert coord.workspaces["wsp_pay"].tasks[task_id].review_required is True


def test_an_override_runs_the_call_with_the_reviewers_arguments(setup):
    coord, bridge, agent, executed = setup
    call, results, _ = decide_and_resume(
        coord, bridge, agent, "decide.override",
        diff=[{"op": "replace", "path": "/args/amount", "value": 50}],
        rationale="over the desk limit")
    assert results.approvals[call.tool_call_id].override_args == {
        **call.args_as_dict(), "amount": 50}
    assert [{k: v for k, v in run.items() if k != "metadata"} for run in executed] == [
        {**call.args_as_dict(), "amount": 50}]


def test_a_rejection_denies_the_call_with_the_reviewers_note(setup):
    coord, bridge, agent, executed = setup
    call, results, final = decide_and_resume(
        coord, bridge, agent, "decide.reject", comment="not on the allow-list")
    denied = results.approvals[call.tool_call_id]
    assert isinstance(denied, ToolDenied)
    assert denied.message == "not on the allow-list"
    assert executed == []
    assert "not on the allow-list" in final.output


def test_an_edit_to_the_call_itself_is_not_run(setup):
    """The run can apply new arguments, and nothing else: an edit naming
    another tool is denied, and nothing runs."""
    coord, bridge, agent, executed = setup
    call, results, _ = decide_and_resume(
        coord, bridge, agent, "decide.override",
        diff=[{"op": "replace", "path": "/tool", "value": "refund"}],
        rationale="refund it")
    assert isinstance(results.approvals[call.tool_call_id], ToolDenied)
    assert executed == []
