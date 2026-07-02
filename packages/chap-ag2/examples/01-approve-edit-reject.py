"""
Example: approve, edit, and reject an AG2 (AutoGen) agent's messages at
the human-input turn, and read the CHAP audit chain that results.

Run (needs the optional AG2 extra):
    pip install -e ".[ag2]"
    python3 examples/01-approve-edit-reject.py

The assistant runs without an LLM (a fixed draft). A UserProxyAgent
records each human turn through the bridge from inside get_human_input,
where both the message under review and the reply are available. The
decision is scripted here, standing in for a UI that captures the human's
intent -- AG2's reply text alone can't tell an edit from plain dialogue.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from autogen import ConversableAgent, UserProxyAgent

from chap_coordinator import Coordinator
from chap_ag2 import ChapTurnBridge


TURNS = [
    {"reply": "", "decision": None},                     # empty -> approve
    # Refining edit: same decision, smaller amount -> intent_preserved true.
    {"reply": "refund $50 to Alice", "decision": "override",
     "approver": "human:sam@example.org",
     "rationale": "over the desk limit; capped to 50", "tags": ["limit-exceeded"]},
    # Substituting edit: escalate instead of refunding, a different
    # decision, so intent_preserved is false.
    {"reply": "escalate to a supervisor instead of refunding", "decision": "override",
     "approver": "human:sam@example.org",
     "rationale": "not a refund call; escalating", "tags": ["substituted"],
     "intent_preserved": False},
    {"reply": "recipient is not on the allow-list", "decision": "reject"},
    {"reply": "exit", "decision": None},                 # records nothing
]


def build_user(bridge: ChapTurnBridge) -> UserProxyAgent:
    script = list(TURNS)

    class RecordingUser(UserProxyAgent):
        def get_human_input(self, prompt, **kw):
            turn = script.pop(0) if script else {"reply": "exit", "decision": None}
            bridge.record_turn(
                self.last_message(), turn["reply"],
                decision=turn.get("decision"),
                approver=turn.get("approver"),
                rationale=turn.get("rationale"),
                tags=turn.get("tags"),
                intent_preserved=turn.get("intent_preserved"),
            )
            return turn["reply"]

    return RecordingUser("user", human_input_mode="ALWAYS",
                         code_execution_config=False, default_auto_reply="")


def main() -> None:
    coord = Coordinator()
    bridge = ChapTurnBridge(
        coord,
        workspace="wsp_support",
        agent="agent:assistant#v1",
        reviewer="human:alice@example.org",
    )

    assistant = ConversableAgent(
        "assistant", llm_config=False,
        default_auto_reply="Draft: refund $100 to Alice.",
    )
    user = build_user(bridge)
    user.initiate_chat(assistant, message="please draft a refund", max_turns=6)

    print("\nAudit chain")
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
    main()
