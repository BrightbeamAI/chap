"""
Example: one approval-gated Pydantic AI tool, decided at a CHAP desk, and
the CHAP audit chain that results.

Run (needs the optional Pydantic AI extra):
    pip install -e ".[pydantic-ai]"
    python3 examples/01-approve-edit-deny.py

It uses Pydantic AI's TestModel, so it runs offline with no API key. The
model proposes a `transfer` call, and each run pauses for approval. The
bridge opens a review of the call and waits; Sam decides it at a desk that
shares the coordinator, where a function stands in for the desk: an
approval, an edit that caps the amount, an edit that sends the money
elsewhere, and a denial. The run carries out each decision. Last, a
decision made in this script's own interface is recorded in Sam's name,
marked as recorded.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ToolApproved
from pydantic_ai.models.test import TestModel

from chap_coordinator import Coordinator
from chap_pydantic_ai import ChapApprovalBridge

WORKSPACE = "wsp_payments"
SAM = "human:sam@example.org"

agent = Agent(TestModel(), output_type=[str, DeferredToolRequests])
coord = Coordinator(enable_chain=True)
bridge = ChapApprovalBridge(coord, workspace=WORKSPACE, agent="agent:assistant#v1", reviewer=SAM)


@agent.tool_plain(requires_approval=True)
def transfer(amount: int, to: str) -> str:
    return f"sent {amount} to {to}"


def sam_decides(method: str, **params) -> None:
    """Sam's desk: it waits for the next review and decides it in Sam's
    name."""
    def call(name, **more):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"desk-{time.monotonic_ns()}", "method": name,
                               "params": {"workspace": WORKSPACE, "from": SAM, **more}})

    decided = {e["envelope"]["params"]["task_id"] for e in call("audit.read")["result"]["entries"]
               if "envelope" in e and e["envelope"]["method"].startswith("decide.")}
    while True:
        submitted = [e["envelope"]["params"] for e in call("audit.read", filter={"method": "task.complete"})
                     ["result"]["entries"] if e["envelope"]["params"]["task_id"] not in decided]
        if submitted:
            break
        time.sleep(0.05)
    print(f"  Sam sees {submitted[0]['output']['args']} and decides {method}")
    call(method, task_id=submitted[0]["task_id"], **params)


def run(method: str, **params) -> None:
    first = agent.run_sync("move some money")
    desk = threading.Thread(target=sam_decides, args=(method,), kwargs=params)
    desk.start()
    results = bridge.request_results(first.output, poll_seconds=0.1)
    desk.join()
    final = agent.run_sync("move some money", message_history=first.all_messages(),
                           deferred_tool_results=results)
    print(f"  the run ends with {final.output}")


def main() -> None:
    run("decide.approve", comment="fine")
    run("decide.override", rationale="over the desk limit; capped to 50", tags=["limit-exceeded"],
        diff=[{"op": "replace", "path": "/args/amount", "value": 50}])
    run("decide.override", rationale="wrong recipient; routed to escrow", tags=["wrong-recipient"],
        intent_preserved=False, diff=[{"op": "replace", "path": "/args/to", "value": "acct-escrow"}])
    run("decide.reject", comment="recipient not on the allow-list")

    # A decision made in this script's own interface, recorded in Sam's name.
    first = agent.run_sync("move some money")
    call = first.output.approvals[0]
    results = DeferredToolResults(approvals={call.tool_call_id: ToolApproved()},
                                  metadata={call.tool_call_id: {"rationale": "approved on the phone"}})
    bridge.record_results(first.output, results)
    final = agent.run_sync("move some money", message_history=first.all_messages(), deferred_tool_results=results)
    print(f"  recorded: approved on the phone; the run ends with {final.output}")

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
    main()
