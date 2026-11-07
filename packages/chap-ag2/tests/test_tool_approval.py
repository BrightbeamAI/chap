"""
chap-ag2 with AG2 1.x: tool_approval() gates a tool, a scripted model (no
LLM) calls it, a person decides at a desk on the same event loop, and the
tool runs as they decided. The tool records what it ran with.

Skipped where AG2 1.x (the `ag2` package) is not installed;
scripts/test-bridges.sh installs it, and fails where they are skipped.
"""

import asyncio
import json
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

pytest.importorskip("ag2.testing")

from ag2 import Agent, tool
from ag2.events import ToolCallEvent
from ag2.testing import TestConfig

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_ag2 import ChapTurnBridge

WS = "wsp_ag2"
REVIEWER = "human:alice@example.org"
PROPOSED = {"amount": 100, "to": "Alice"}


async def desk(coord, method, delay=0.0, **params):
    """A person at a desk sharing the coordinator: they wait for the review
    and decide it in their own name, after ``delay`` seconds."""
    def call(name, **more):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"desk-{name}", "method": name,
                               "params": {"workspace": WS, "from": REVIEWER, **more}})

    for _ in range(500):
        submitted = call("audit.read", filter={"method": "task.complete"})["result"]["entries"]
        if submitted:
            await asyncio.sleep(delay)
            return call(method, task_id=submitted[0]["envelope"]["params"]["task_id"], **params)
        await asyncio.sleep(0.01)
    raise AssertionError("no review reached the desk")


def pay(method, timeout=5, delay=0.0, ask_timeout=None, **params):
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapTurnBridge(coord, workspace=WS, agent="agent:payer#v1", reviewer=REVIEWER)
    ran = []

    @tool(middleware=[bridge.tool_approval(timeout=timeout, poll_seconds=0.01)])
    def transfer(amount: int, to: str) -> str:
        """Send money."""
        ran.append({"amount": amount, "to": to})
        return f"sent {amount} to {to}"

    agent = Agent("payer", tools=[transfer], config=TestConfig(
        ToolCallEvent("transfer", arguments=json.dumps(PROPOSED)), "done"))

    async def main():
        person = asyncio.ensure_future(desk(coord, method, delay, **params))
        if ask_timeout is None:
            await agent.ask("pay Alice")
        else:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(agent.ask("pay Alice"), ask_timeout)
        return await person

    answered = asyncio.run(main())
    return ran, bridge, coord, answered


def task(coord):
    (only,) = coord.workspaces[WS].tasks.values()
    return only


def test_an_approval_runs_the_call_as_proposed():
    ran, bridge, coord, _ = pay("decide.approve")
    assert ran == [PROPOSED]
    assert task(coord).review_required is True and task(coord).output["args"] == PROPOSED
    decided = [e["envelope"]["params"]["from"] for e in bridge.audit()
               if "envelope" in e and e["envelope"]["method"].startswith("decide.")]
    assert decided == [REVIEWER]


def test_an_override_runs_the_reviewers_arguments():
    ran, _, _, _ = pay("decide.override", rationale="over the desk limit",
                       diff=[{"op": "replace", "path": "/args/amount", "value": 50}])
    assert ran == [{"amount": 50, "to": "Alice"}]


def test_a_rejection_runs_nothing():
    ran, _, _, _ = pay("decide.reject", comment="not on the allow-list")
    assert ran == []


@pytest.mark.parametrize("diff", [
    [{"op": "replace", "path": "/tool", "value": "refund"}],     # another tool
    [{"op": "replace", "path": "/args", "value": None}],         # the arguments made null
    [{"op": "remove", "path": "/args"}],                         # or taken away
])
def test_an_approved_edit_the_run_cannot_carry_out_runs_nothing(diff):
    ran, _, _, _ = pay("decide.override", diff=diff, rationale="edit")
    assert ran == []


def test_a_revision_runs_nothing_and_closes_its_task():
    ran, _, coord, _ = pay("decide.reject", comment="split it", request_revision=True)
    assert ran == [] and task(coord).state == "declined"


def test_a_call_nobody_decides_in_time_runs_nothing_and_its_review_is_withdrawn():
    ran, _, coord, answered = pay("decide.approve", timeout=0.2, delay=0.5)
    # The person's approval came after the agent stopped waiting, and was refused.
    assert answered["error"]["code"] == -32010
    assert ran == [] and task(coord).state == "in_progress"


def test_a_cancelled_turn_withdraws_its_review():
    ran, _, coord, answered = pay("decide.approve", timeout=None, delay=0.5, ask_timeout=0.2)
    assert answered["error"]["code"] == -32010
    assert ran == [] and task(coord).state == "in_progress"
