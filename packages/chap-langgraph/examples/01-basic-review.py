"""
Example 1: basic review with chap-langgraph.

A drafter agent produces a one-line reply. A human reviewer approves
it. The CHAP audit chain captures the whole exchange.

Run:
    python3 examples/01-basic-review.py

This example does NOT require LangGraph to be installed; the bridge
works in pure Python. The point is to show what the audit trail looks
like for a single human-in-the-loop checkpoint.
"""

import sys
from pathlib import Path

# Allow this example to run from the repo without installing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from chap_coordinator import Coordinator
from chap_langgraph import ChapBridge, hil_review


def main() -> None:
    coord = Coordinator()
    bridge = ChapBridge(
        coord,
        workspace="wsp_demo",
        agent="agent:drafter#v1",
        reviewer="human:alice@example.org",
    )

    # 1. The agent produces a draft and asks for human review.
    draft = {"reply": "Sorry to hear that. We'll refund your order."}
    state = hil_review(bridge, draft, kind="customer_response")
    print(f"  draft submitted, task_id = {state['chap_task_id']}")

    # 2. The reviewer approves as-is.
    bridge.apply_decision(state["chap_task_id"], "approve")
    print("  human approved")

    # 3. Inspect the audit chain.
    print("\nAudit chain:")
    for entry in bridge.audit():
        env = entry["envelope"]
        method = env["method"]
        params = env.get("params", {})
        actor = params.get("from", "")
        print(f"  seq={entry['seq']:<3} {method:<25} {actor}")


if __name__ == "__main__":
    main()
