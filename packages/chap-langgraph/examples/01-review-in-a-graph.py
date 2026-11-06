"""
Example: a LangGraph graph whose drafts a person reviews, with every
decision on the CHAP audit chain.

Run (needs the optional LangGraph extra):
    pip install -e ".[langgraph]"
    python3 examples/01-review-in-a-graph.py

LangGraph's fake chat model drafts each reply, so it runs offline with no
API key. The graph has three nodes: `draft` writes the reply and opens the
review, `review` pauses on interrupt() and applies the decision when the
graph resumes, and `send` sends what was approved. Three tickets run on
three threads: one approved, one edited, one rejected.
"""

import sys
from pathlib import Path
from typing import Any, Optional, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from chap_coordinator import Coordinator
from chap_langgraph import ChapBridge, ReviewRejected, hil_review


coord = Coordinator(enable_chain=True)
bridge = ChapBridge(
    coord,
    workspace="wsp_support",
    agent="agent:drafter#v1",
    reviewer="human:alice@example.org",
)
model = GenericFakeChatModel(messages=iter([
    AIMessage(content="We will refund your order today."),
    AIMessage(content="Refund processed."),
    AIMessage(content="We will refund you twice the price."),
]))
outbox: list = []


class State(TypedDict, total=False):
    ticket: str
    reply: Any
    rejected: Optional[str]
    chap_task_id: str
    chap_artefact: Any
    chap_artefact_digest: str
    chap_kind: str


def draft(state: State) -> State:
    """Draft the reply and open its review. This node finishes before the
    graph pauses, so resuming does not draft again."""
    reply = {"reply": model.invoke(state["ticket"]).content}
    return hil_review(bridge, reply, kind="draft_reply", input_={"ticket": state["ticket"]})


def review(state: State) -> State:
    """Pause until the reviewer decides, then apply the decision. This node
    runs again from the top when the graph resumes."""
    decision = interrupt({"task_id": state["chap_task_id"],
                          "draft": state["chap_artefact"],
                          "digest": state["chap_artefact_digest"]})
    try:
        reply = bridge.apply_decision(state["chap_task_id"], decision,
                                      state["chap_artefact"])
    except ReviewRejected as rejected:
        return {"reply": None, "rejected": rejected.rationale}
    return {"reply": reply}


def send(state: State) -> State:
    if state.get("reply") is not None:
        outbox.append(state["reply"]["reply"])
    return {}


builder = StateGraph(State)
builder.add_node("draft", draft)
builder.add_node("review", review)
builder.add_node("send", send)
builder.add_edge(START, "draft")
builder.add_edge("draft", "review")
builder.add_edge("review", "send")
builder.add_edge("send", END)
graph = builder.compile(checkpointer=InMemorySaver())


def handle(thread: str, ticket: str, decision: Any) -> None:
    config = {"configurable": {"thread_id": thread}}
    paused = graph.invoke({"ticket": ticket}, config)
    shown = paused["__interrupt__"][0].value
    print(f"  {thread}: the reviewer sees {shown['draft']['reply']!r}")
    final = graph.invoke(Command(resume=decision), config)
    outcome = final["reply"]["reply"] if final.get("reply") else f"rejected: {final['rejected']}"
    print(f"  {thread}: {outcome}")


def main() -> None:
    handle("ticket-1", "Where is my refund?", "approve")
    handle("ticket-2", "My order arrived broken.", {
        "diff": [{"op": "replace", "path": "/reply",
                  "value": "I am sorry it arrived broken. Your refund is on its way."}],
        "rationale": "too curt for an upset customer",
        "tags": ["tone-warmed"],
        "intent_preserved": True,
    })
    handle("ticket-3", "Can I have a discount?",
           {"action": "reject", "rationale": "we do not refund twice"})

    print(f"\nSent: {outbox}")
    print("\nAudit chain")
    print("===========")
    for entry in bridge.audit():
        # A refused call is recorded under "request", with its outcome.
        env = entry.get("envelope") or entry["request"]
        params = env.get("params", {})
        detail = ""
        if "outcome" in entry:
            detail = f"  refused {entry['outcome']['code']}"
        elif env["method"].startswith("decide."):
            detail = f"  {params.get('comment') or params.get('rationale') or ''}"
        print(f"  seq={entry['seq']:<3} {env['method']:<17} {params.get('from', '')}{detail}")
    verified = coord.dispatch({"jsonrpc": "2.0", "id": "v", "method": "audit.verify_chain",
                               "params": {"workspace": "wsp_support"}})
    print(f"\nChain verified: {verified['result']}")


if __name__ == "__main__":
    main()
