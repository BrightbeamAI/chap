"""
chap-llama-index with a real LlamaIndex Workflow: a step proposes a
payment and pauses on an InputRequiredEvent, the bridge opens a review and
waits, a person decides at a desk on the same event loop, and the
HumanResponseEvent the bridge returns resumes the workflow.

Skipped where llama-index-workflows is not installed;
scripts/test-bridges.sh installs it.
"""

import asyncio
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

pytest.importorskip("workflows")

from workflows import Context, Workflow, step
from workflows.events import HumanResponseEvent, InputRequiredEvent, StartEvent, StopEvent

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_llama_index import ChapHitlBridge


REVIEWER = "human:alice@example.org"
PROPOSED = {"amount": 100, "to": "acct-9"}


class PaymentReview(Workflow):
    def __init__(self, paid: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self.paid = paid

    @step
    async def propose(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
        return InputRequiredEvent(prefix="approve payment? ", proposed=PROPOSED)

    @step
    async def pay(self, ctx: Context, ev: HumanResponseEvent) -> StopEvent:
        if ev.get("decision") in ("approve", "override"):
            self.paid.append(ev.get("response"))
            return StopEvent(result=f"paid {ev.get('response')}")
        return StopEvent(result=f"not paid: {ev.get('response')}")


async def desk(coord: Coordinator, method: str, **params) -> None:
    """A person at a desk sharing the coordinator: they join, wait for the
    review addressed to them, and decide it."""
    coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "wsp_li", "from": REVIEWER, "type": "human"}})
    for _ in range(500):
        for task in list(coord.workspaces["wsp_li"].tasks.values()):
            if task.state == "review_requested":
                out = coord.dispatch({"jsonrpc": "2.0", "id": "d", "method": method,
                                      "params": {"workspace": "wsp_li", "from": REVIEWER,
                                                 "task_id": task.id, **params}})
                assert "error" not in out, out
                return
        await asyncio.sleep(0.01)
    raise AssertionError("no review reached the desk")


def run(method: str, **params):
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    bridge = ChapHitlBridge(coord, workspace="wsp_li", agent="agent:writer#v1",
                            reviewer=REVIEWER)
    paid: list = []
    responses: list = []

    async def main():
        person = asyncio.ensure_future(desk(coord, method, **params))
        handler = PaymentReview(paid, timeout=None).run()
        async for event in handler.stream_events():
            if isinstance(event, InputRequiredEvent):
                response = await bridge.request_response(event, timeout=5,
                                                         poll_seconds=0.01)
                responses.append(response)
                handler.ctx.send_event(response)
        result = await handler
        await person
        return result

    result = asyncio.run(main())
    return result, paid, responses[0], bridge


def test_an_approval_resumes_the_workflow_with_the_proposed_output():
    result, paid, response, bridge = run("decide.approve", comment="fine")
    assert paid == [PROPOSED]
    assert result == f"paid {PROPOSED}"
    assert (response.get("decision"), response.get("user_name")) == ("approve", REVIEWER)
    decided = [e["envelope"]["params"]["from"] for e in bridge.audit()
               if e["envelope"]["method"].startswith("decide.")]
    assert decided == [REVIEWER]


def test_an_override_resumes_it_with_the_reviewers_version():
    _, paid, response, _ = run(
        "decide.override",
        diff=[{"op": "replace", "path": "/amount", "value": 50}], rationale="capped")
    assert paid == [{"amount": 50, "to": "acct-9"}]
    assert (response.get("decision"), response.get("rationale")) == ("override", "capped")


def test_a_rejection_pays_nothing():
    result, paid, response, _ = run("decide.reject", comment="not on the allow-list")
    assert paid == []
    assert result == "not paid: not on the allow-list"
    assert response.get("decision") == "reject"
    assert response.get("chap_task_id", "").startswith("tsk_")
