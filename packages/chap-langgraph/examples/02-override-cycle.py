"""
Example 2: override cycle with chap-langgraph.

A drafter agent writes a curt reply. The human reviewer edits the tone
and records the change as a structured override. The audit chain
captures the diff, the rationale, and the tags - these are the
ingredients for "your overrides become training data for free".

Run:
    python3 examples/02-override-cycle.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from chap_coordinator import Coordinator
from chap_langgraph import ChapBridge, hil_review


def main() -> None:
    coord = Coordinator()
    bridge = ChapBridge(
        coord,
        workspace="wsp_demo_override",
        agent="agent:drafter#v2",
        reviewer="human:bob@example.org",
    )

    # Agent draft: technically correct but cold.
    draft = {
        "reply": "Refund processed.",
        "tone":  "curt",
    }
    state = hil_review(bridge, draft, kind="customer_response")
    print(f"  draft submitted, task_id = {state['chap_task_id']}")

    # Reviewer edits the tone.
    applied = bridge.apply_decision(state["chap_task_id"], {
        "diff": [
            {"op": "replace", "path": "/reply",
             "value": "I'm sorry for the trouble. Your refund has been processed."},
            {"op": "replace", "path": "/tone",
             "value": "warm"},
        ],
        "rationale":        "tone too procedural for an upset customer",
        "tags":             ["tone-warmed", "customer-empathy"],
        "intent_preserved": True,  # same decision, better expression
    })
    print(f"  human applied override → {applied['reply']!r}")

    # The audit chain now contains the full provenance.
    print("\nAudit chain:")
    for entry in bridge.audit():
        env = entry["envelope"]
        method = env["method"]
        params = env.get("params", {})
        extra = ""
        if method == "decide.override":
            extra = (f"  tags={params.get('tags')} "
                     f"rationale={params.get('rationale')!r}")
        print(f"  seq={entry['seq']:<3} {method:<25} {params.get('from','')}{extra}")


if __name__ == "__main__":
    main()
