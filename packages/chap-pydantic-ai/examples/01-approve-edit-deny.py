"""
Example: approve, edit, and deny one approval-gated Pydantic AI tool,
and read the CHAP audit chain that results.

Run (needs the optional Pydantic AI extra):
    pip install -e ".[pydantic-ai]"
    python3 examples/01-approve-edit-deny.py

It uses Pydantic AI's TestModel, so it runs offline with no API key. The
model proposes a `transfer` call; each run pauses for approval, and the
bridge records the decision this script makes for the human (approve,
edited args, or denial) as decide.approve / decide.override /
decide.reject, on a hash-linked chain.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from pydantic_ai import (
    Agent, DeferredToolRequests, DeferredToolResults, ToolApproved, ToolDenied,
)
from pydantic_ai.models.test import TestModel

from chap_coordinator import Coordinator
from chap_pydantic_ai import ChapApprovalBridge


agent = Agent(TestModel(), output_type=[str, DeferredToolRequests])


@agent.tool_plain(requires_approval=True)
def transfer(amount: int, to: str) -> str:
    return f"sent {amount} to {to}"


def decide(bridge: ChapApprovalBridge, resolve) -> None:
    """Run the agent to its pause, let `resolve` make the decision, record
    it, and resume the run with the same results, so what is recorded is
    what runs."""
    first = agent.run_sync("move some money")
    requests = first.output
    results = resolve(requests.approvals[0])
    bridge.record_results(requests, results)
    final = agent.run_sync("move some money", message_history=first.all_messages(),
                           deferred_tool_results=results)
    print(f"  the run ends with {final.output}")


def approve(call) -> DeferredToolResults:
    return DeferredToolResults(approvals={call.tool_call_id: ToolApproved()})


def cap_the_amount(call) -> DeferredToolResults:
    """Refining edit: same decision, capped amount. intent_preserved stays
    true (the default), and the metadata carries the why."""
    return DeferredToolResults(
        approvals={call.tool_call_id: ToolApproved(
            override_args={**call.args_as_dict(), "amount": 50})},
        metadata={call.tool_call_id: {
            "approver":  "human:sam@example.org",
            "rationale": "over the desk limit; capped to 50",
            "tags":      ["limit-exceeded"],
        }},
    )


def redirect(call) -> DeferredToolResults:
    """Substituting edit: the reviewer redirects the payment, a different
    decision than the agent's, so intent_preserved=false."""
    return DeferredToolResults(
        approvals={call.tool_call_id: ToolApproved(
            override_args={**call.args_as_dict(), "to": "acct-escrow"})},
        metadata={call.tool_call_id: {
            "approver":         "human:sam@example.org",
            "rationale":        "wrong recipient; routed to escrow",
            "tags":             ["wrong-recipient"],
            "intent_preserved": False,
        }},
    )


def deny(call) -> DeferredToolResults:
    return DeferredToolResults(approvals={
        call.tool_call_id: ToolDenied("recipient not on the allow-list")})


def main() -> None:
    coord = Coordinator(enable_chain=True)
    bridge = ChapApprovalBridge(
        coord,
        workspace="wsp_payments",
        agent="agent:assistant#v1",
        reviewer="human:alice@example.org",
    )

    for resolve in (approve, cap_the_amount, redirect, deny):
        decide(bridge, resolve)

    print("\nAudit chain")
    print("===========")
    for entry in bridge.audit():
        # A refused call is recorded under "request", with its outcome.
        env = entry.get("envelope") or entry["request"]
        params = env.get("params", {})
        who = params.get("from", "")
        detail = ""
        if "outcome" in entry:
            detail = f"  refused {entry['outcome']['code']}"
        elif env["method"] == "decide.override":
            detail = (f"  intent_preserved={params['intent_preserved']}"
                      f" diff={params['diff']} tags={params['tags']}")
        elif env["method"] in ("decide.approve", "decide.reject"):
            detail = f"  {params.get('comment', '')}"
        print(f"  seq={entry['seq']:<2} {env['method']:<16} {who}{detail}")
    verified = coord.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                               "params": {"workspace": "wsp_payments"}})
    print(f"\nChain verified: {verified['result']['ok']}")


if __name__ == "__main__":
    main()
