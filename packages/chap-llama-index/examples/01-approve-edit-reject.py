"""
Example: approve, edit, and reject one LlamaIndex Workflow step that
pauses for human input, and read the CHAP audit chain that results.

Run (needs the optional LlamaIndex extra):
    pip install -e ".[llama-index]"
    python3 examples/01-approve-edit-reject.py

The workflow proposes a payment and pauses on an InputRequiredEvent. The
driver streams events, records each human decision through the bridge,
and sends a HumanResponseEvent back to resume the run -- as
decide.approve / decide.override / decide.reject.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from workflows import Context, Workflow, step
from workflows.events import (
    HumanResponseEvent, InputRequiredEvent, StartEvent, StopEvent,
)

from chap_coordinator import Coordinator
from chap_llama_index import ChapHitlBridge


PROPOSED = {"amount": 100, "to": "acct-9"}


class PaymentReview(Workflow):
    @step
    async def propose(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
        return InputRequiredEvent(prefix="approve payment? ", proposed=PROPOSED)

    @step
    async def resume(self, ctx: Context, ev: HumanResponseEvent) -> StopEvent:
        return StopEvent(result=ev.get("response"))


async def run_once(bridge: ChapHitlBridge, response: HumanResponseEvent) -> None:
    """Drive the workflow to its pause, record the decision, resume it."""
    handler = PaymentReview(timeout=30).run()
    async for event in handler.stream_events():
        if isinstance(event, InputRequiredEvent):
            bridge.record_decision(event, response, decision=response.get("decision"))
            handler.ctx.send_event(response)
    await handler


async def main() -> None:
    coord = Coordinator()
    bridge = ChapHitlBridge(
        coord,
        workspace="wsp_payments",
        agent="agent:writer#v1",
        reviewer="human:alice@example.org",
    )

    # Approve as proposed.
    await run_once(bridge, HumanResponseEvent(response="ok", decision="approve"))

    # Refining edit: cap the amount, same decision -> intent_preserved true.
    await run_once(bridge, HumanResponseEvent(
        response={**PROPOSED, "amount": 50}, decision="override",
        user_name="human:sam@example.org",
        rationale="over the desk limit; capped to 50", tags=["limit-exceeded"]))

    # Substituting edit: redirect the payment, a different decision, so
    # intent_preserved is false.
    await run_once(bridge, HumanResponseEvent(
        response={**PROPOSED, "to": "acct-escrow"}, decision="override",
        user_name="human:sam@example.org",
        rationale="wrong recipient; routed to escrow instead",
        tags=["wrong-recipient"], intent_preserved=False))

    # Reject.
    await run_once(bridge, HumanResponseEvent(
        response="recipient not on the allow-list", decision="reject"))

    print("Audit chain")
    print("===========")
    for entry in bridge.audit():
        env = entry["envelope"]
        params = env.get("params", {})
        who = params.get("from", "")
        detail = ""
        if env["method"] == "decide.override":
            detail = (f"  intent_preserved={params['intent_preserved']}"
                      f" diff={params['diff']} tags={params['tags']}")
        elif env["method"] in ("decide.approve", "decide.reject"):
            detail = f"  {params.get('comment', '')}"
        print(f"  seq={entry['seq']:<2} {env['method']:<16} {who}{detail}")


if __name__ == "__main__":
    asyncio.run(main())
