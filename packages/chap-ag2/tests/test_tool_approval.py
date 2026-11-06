"""
chap-ag2 with AG2 1.x: tool_approval() gates a tool, a scripted model (no
LLM) calls it, a person decides at a desk on the same event loop, and the
tool runs as they decided. The tool records what it ran with.

Skipped where AG2 1.x (the `ag2` package) is not installed;
scripts/test-bridges.sh installs it.
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


REVIEWER = "human:alice@example.org"
PROPOSED = {"amount": 100, "to": "Alice"}


async def desk(coord, method, **params):
    """A person at a desk sharing the coordinator: they join, wait for the
    review addressed to them, and decide it."""
    coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "wsp_ag2", "from": REVIEWER, "type": "human"}})
    for _ in range(500):
        for task in list(coord.workspaces["wsp_ag2"].tasks.values()):
            if task.state == "review_requested":
                out = coord.dispatch({"jsonrpc": "2.0", "id": "d", "method": method,
                                      "params": {"workspace": "wsp_ag2", "from": REVIEWER,
                                                 "task_id": task.id, **params}})
                assert "error" not in out, out
                return
        await asyncio.sleep(0.01)
    raise AssertionError("no review reached the desk")


def pay(method, **params):
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapTurnBridge(coord, workspace="wsp_ag2", agent="agent:payer#v1",
                            reviewer=REVIEWER)
    ran = []

    @tool(middleware=[bridge.tool_approval(timeout=5, poll_seconds=0.01)])
    def transfer(amount: int, to: str) -> str:
        """Send money."""
        ran.append({"amount": amount, "to": to})
        return f"sent {amount} to {to}"

    agent = Agent("payer", tools=[transfer], config=TestConfig(
        ToolCallEvent("transfer", arguments=json.dumps(PROPOSED)), "done"))

    async def main():
        person = asyncio.ensure_future(desk(coord, method, **params))
        await agent.ask("pay Alice")
        await person

    asyncio.run(main())
    return ran, bridge, coord


def test_an_approval_runs_the_call_as_proposed():
    ran, bridge, coord = pay("decide.approve")
    assert ran == [PROPOSED]
    (task,) = coord.workspaces["wsp_ag2"].tasks.values()
    assert task.review_required is True and task.output["args"] == PROPOSED
    decided = [e["envelope"]["params"]["from"] for e in bridge.audit()
               if e["envelope"]["method"].startswith("decide.")]
    assert decided == [REVIEWER]


def test_an_override_runs_the_reviewers_arguments():
    ran, _, _ = pay("decide.override",
                    diff=[{"op": "replace", "path": "/args/amount", "value": 50}],
                    rationale="over the desk limit")
    assert ran == [{"amount": 50, "to": "Alice"}]


def test_a_rejection_runs_nothing():
    ran, _, _ = pay("decide.reject", comment="not on the allow-list")
    assert ran == []


def test_an_edit_to_the_call_itself_runs_nothing():
    ran, _, _ = pay("decide.override",
                    diff=[{"op": "replace", "path": "/tool", "value": "refund"}],
                    rationale="refund it")
    assert ran == []
