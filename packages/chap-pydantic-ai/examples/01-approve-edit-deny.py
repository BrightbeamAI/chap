"""
Example: approve, edit, and deny one approval-gated Pydantic AI tool,
and read the CHAP audit chain that results.

Run (needs the optional Pydantic AI extra):
    pip install -e ".[pydantic-ai]"
    python3 examples/01-approve-edit-deny.py

It uses Pydantic AI's TestModel, so it runs offline with no API key. The
model proposes a `transfer` call; each run pauses for approval, and the
bridge records the human's decision -- approve, edited args, or denial --
as decide.approve / decide.override / decide.reject.
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


def propose() -> DeferredToolRequests:
    """Run the agent until it pauses for approval, return the requests."""
    return agent.run_sync("move some money").output


def main() -> None:
    coord = Coordinator()
    bridge = ChapApprovalBridge(
        coord,
        workspace="wsp_payments",
        agent="agent:assistant#v1",
        reviewer="human:alice@example.org",
    )

    # 1. Approve as proposed.
    requests = propose()
    results = DeferredToolResults()
    results.approvals[requests.approvals[0].tool_call_id] = ToolApproved()
    bridge.record_results(requests, results)

    # 2. Approve, but cap the amount. The edit is the diff; a senior
    #    reviewer signs it, and the metadata carries the why.
    requests = propose()
    call = requests.approvals[0]
    results = DeferredToolResults()
    results.approvals[call.tool_call_id] = ToolApproved(
        override_args={**call.args_as_dict(), "amount": 50},
    )
    results.metadata = {call.tool_call_id: {
        "approver":  "human:sam@example.org",
        "rationale": "over the desk limit; capped to 50",
        "tags":      ["limit-exceeded"],
    }}
    bridge.record_results(requests, results)

    # 3. Deny.
    requests = propose()
    results = DeferredToolResults()
    results.approvals[requests.approvals[0].tool_call_id] = ToolDenied(
        "recipient not on the allow-list",
    )
    bridge.record_results(requests, results)

    print("Audit chain")
    print("===========")
    for entry in bridge.audit():
        env = entry["envelope"]
        params = env.get("params", {})
        who = params.get("from", "")
        detail = ""
        if env["method"] == "decide.override":
            detail = f"  diff={params['diff']} tags={params['tags']}"
        elif env["method"] in ("decide.approve", "decide.reject"):
            detail = f"  {params.get('comment', '')}"
        print(f"  seq={entry['seq']:<2} {env['method']:<16} {who}{detail}")


if __name__ == "__main__":
    main()
