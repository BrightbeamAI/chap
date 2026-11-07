"""
Example: an AG2 (AutoGen) agent's messages, decided at a CHAP desk at the
human-input turn, and the CHAP audit chain that results.

Run (needs the optional AG2 extra):
    pip install -e ".[ag2]"
    python3 examples/01-approve-edit-reject.py

The assistant runs without an LLM, answering each turn with a draft. A
UserProxyAgent asks the bridge for the human's reply from inside
get_human_input, and the bridge opens a review of the message and waits.
Sam decides at a desk that shares the coordinator, where a thread stands in
for the desk: an approval, an edit, a revision, and a rejection, which ends
the chat. Last, a turn taken in this script's own interface is recorded in
Sam's name, marked as recorded.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from autogen import ConversableAgent, UserProxyAgent

from chap_coordinator import Coordinator
from chap_ag2 import ChapTurnBridge, ReviewRejected

WORKSPACE = "wsp_support"
SAM = "human:sam@example.org"

coord = Coordinator(enable_chain=True)
bridge = ChapTurnBridge(coord, workspace=WORKSPACE, agent="agent:assistant#v1", reviewer=SAM)
DECISIONS = [
    ("decide.approve", {"comment": "fine"}),
    ("decide.override", {"rationale": "over the desk limit; capped to 50", "tags": ["limit-exceeded"],
                         "diff": [{"op": "replace", "path": "", "value": "Draft: refund $50 to Alice."}]}),
    ("decide.reject", {"comment": "ask for the order number first", "request_revision": True}),
    ("decide.reject", {"comment": "recipient is not on the allow-list"}),
]


def sams_desk() -> None:
    """Sam's desk: it decides each review in turn, in Sam's name."""
    def call(method, **params):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"desk-{method}", "method": method,
                               "params": {"workspace": WORKSPACE, "from": SAM, **params}})

    for done, (method, params) in enumerate(DECISIONS):
        while len(submitted := call("audit.read", filter={"method": "task.complete"})["result"]["entries"]) <= done:
            time.sleep(0.02)
        review = submitted[done]["envelope"]["params"]
        print(f"  Sam sees {review['output']!r} and decides {method}")
        call(method, task_id=review["task_id"], **params)


class ReviewedUser(UserProxyAgent):
    def get_human_input(self, prompt, **kw):
        reply = bridge.request_reply(self.last_message(), poll_seconds=0.05)
        print(f"  the reply to AG2 is {reply!r}")
        return reply


def main() -> None:
    desk = threading.Thread(target=sams_desk)
    desk.start()
    assistant = ConversableAgent("assistant", llm_config=False, default_auto_reply="Draft: refund $100 to Alice.")
    user = ReviewedUser("user", human_input_mode="ALWAYS", code_execution_config=False, default_auto_reply="")
    try:
        user.initiate_chat(assistant, message="please draft a refund", max_turns=10, silent=True)
    except ReviewRejected as rejected:
        print(f"  the chat ends: {rejected}")
    desk.join()

    # A turn taken in this script's own interface, recorded in Sam's name.
    bridge.record_turn({"content": "Draft: refund $20 to Bob."}, "", rationale="approved on the phone")
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
            detail = f"  diff={params['diff']}"
        elif env["method"] in ("decide.approve", "decide.reject"):
            detail = f"  {params.get('comment', '')}"
        elif env["method"] == "task.update":
            detail = f"  {params['state']}: {params.get('progress_note', '')}"
        if params.get("via"):
            detail += f"  via={params['via']}"
        print(f"  seq={entry['seq']:<2} {env['method']:<16} {params.get('from', '')}{detail}")
    verified = coord.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                               "params": {"workspace": WORKSPACE}})
    print(f"\nChain verified: {verified['result']['ok']}")


if __name__ == "__main__":
    main()
