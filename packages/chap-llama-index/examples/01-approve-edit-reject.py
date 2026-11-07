"""
Example: one LlamaIndex Workflow step that pauses for human input, decided
at a CHAP desk, and the CHAP audit chain that results.

Run (needs the optional LlamaIndex extra):
    pip install -e ".[llama-index]"
    python3 examples/01-approve-edit-reject.py

The workflow proposes a payment and pauses on an InputRequiredEvent. The
driver streams its events and hands each pause to the bridge, which opens a
review and waits; Sam decides at a desk that shares the coordinator, where
a task on the event loop stands in for the desk: an approval, an edit that
caps the amount, an edit that sends the money elsewhere, a rejection, and a
revision, after which the workflow proposes a smaller payment on the same
task. Last, a decision made in this script's own interface is recorded in
Sam's name, marked as recorded.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from workflows import Context, Workflow, step
from workflows.events import HumanResponseEvent, InputRequiredEvent, StartEvent, StopEvent

from chap_coordinator import Coordinator
from chap_llama_index import ChapHitlBridge

WORKSPACE = "wsp_payments"
SAM = "human:sam@example.org"
PROPOSED = {"amount": 100, "to": "acct-9"}

coord = Coordinator(enable_chain=True)
bridge = ChapHitlBridge(coord, workspace=WORKSPACE, agent="agent:writer#v1", reviewer=SAM)


class PaymentReview(Workflow):
    @step
    async def propose(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
        return InputRequiredEvent(prefix="approve payment? ", proposed=PROPOSED)

    @step
    async def resume(self, ctx: Context, ev: HumanResponseEvent) -> InputRequiredEvent | StopEvent:
        if ev.get("decision") == "revise":
            # Sent back for another: a smaller payment, the next round of the same review.
            return InputRequiredEvent(prefix="approve payment? ", proposed={**PROPOSED, "amount": 40},
                                      chap_task_id=ev.get("chap_task_id"))
        return StopEvent(result=f"{ev.get('decision')}: {ev.get('response')}")


async def sams_desk(decisions: list) -> None:
    """Sam's desk: it waits for each review in turn and decides it in Sam's name."""
    def call(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"desk-{method}", "method": method,
                               "params": {"workspace": WORKSPACE, "from": SAM, **params}})

    done = len(call("audit.read", filter={"method": "task.complete"})["result"]["entries"])
    for method, params in decisions:
        while len(submitted := call("audit.read", filter={"method": "task.complete"})["result"]["entries"]) <= done:
            await asyncio.sleep(0.02)
        done += 1
        review = submitted[-1]["envelope"]["params"]
        print(f"  Sam sees {review['output']} and decides {method}")
        call(method, task_id=review["task_id"], **params)


async def run(*decisions) -> None:
    desk = asyncio.create_task(sams_desk(list(decisions)))
    handler = PaymentReview(timeout=None).run()
    async for event in handler.stream_events():
        if isinstance(event, InputRequiredEvent):
            handler.ctx.send_event(await bridge.request_response(
                event, task_id=event.get("chap_task_id"), poll_seconds=0.1))
    print(f"  the workflow ends with {await handler}")
    await desk


async def main() -> None:
    await run(("decide.approve", {"comment": "fine"}))
    await run(("decide.override", {"rationale": "over the desk limit; capped to 50", "tags": ["limit-exceeded"],
                                   "diff": [{"op": "replace", "path": "/amount", "value": 50}]}))
    await run(("decide.override", {"rationale": "wrong recipient; routed to escrow", "intent_preserved": False,
                                   "diff": [{"op": "replace", "path": "/to", "value": "acct-escrow"}]}))
    await run(("decide.reject", {"comment": "recipient not on the allow-list"}))
    await run(("decide.reject", {"comment": "smaller, please", "request_revision": True}),
              ("decide.approve", {}))

    # A decision made in this script's own interface, recorded in Sam's name.
    asked = InputRequiredEvent(prefix="approve payment? ", proposed=PROPOSED)
    bridge.record_decision(asked, HumanResponseEvent(response=PROPOSED, rationale="approved on the phone"),
                           decision="approve")
    print("  recorded: approved on the phone")

    print("\nAudit chain")
    print("===========")
    for entry in bridge.audit():
        # A refused call is recorded under "request", with its outcome.
        env = entry.get("envelope") or entry["request"]
        params = env.get("params", {})
        detail = ""
        if "outcome" in entry:
            detail = f"  refused {entry['outcome']['code']}"
        elif env["method"] == "decide.override":
            detail = f"  intent_preserved={params.get('intent_preserved')} diff={params['diff']}"
        elif env["method"] in ("decide.approve", "decide.reject"):
            detail = f"  {params.get('comment', '')}"
        if params.get("via"):
            detail += f"  via={params['via']}"
        print(f"  seq={entry['seq']:<2} {env['method']:<16} {params.get('from', '')}{detail}")
    verified = coord.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                               "params": {"workspace": WORKSPACE}})
    print(f"\nChain verified: {verified['result']['ok']}")


if __name__ == "__main__":
    asyncio.run(main())
