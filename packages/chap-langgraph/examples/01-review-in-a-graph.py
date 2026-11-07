"""
Example: a LangGraph graph whose drafts a person reviews at a CHAP desk,
with every decision on the CHAP audit chain.

Run (needs the optional LangGraph extra):
    pip install -e ".[langgraph]"
    python3 examples/01-review-in-a-graph.py

LangGraph's fake chat model drafts each reply, so it runs offline with no
API key. The graph has three nodes: `draft` writes the reply and opens its
review, `review` pauses on interrupt() while the person decides and, once
the graph resumes, reads their decision from CHAP and applies it, and `send`
sends what was approved. A draft sent back for another goes round again, as
the next round of the same review.

Alice decides at a desk that shares the coordinator; here a function stands
in for her desk. Three tickets run on three threads: one approved, one
edited, and one sent back for another draft, which is approved.
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

WORKSPACE = "wsp_support"
ALICE = "human:alice@example.org"

coord = Coordinator(enable_chain=True)
bridge = ChapBridge(coord, workspace=WORKSPACE, agent="agent:drafter#v1", reviewer=ALICE)
model = GenericFakeChatModel(messages=iter([
    AIMessage(content="We will refund your order today."),
    AIMessage(content="Refund processed."),
    AIMessage(content="We will refund you twice the price, and send a voucher."),
    AIMessage(content="We cannot offer a discount on this order."),
]))
outbox: list = []


class State(TypedDict, total=False):
    ticket: str
    note: Optional[str]
    reply: Any
    rejected: Optional[str]
    chap_task_id: str
    chap_artefact: Any
    chap_artefact_digest: str
    chap_kind: str


def draft(state: State) -> State:
    """Draft the reply and open its review. A draft sent back for another
    opens the next round of the same review. This node finishes before the
    graph pauses, so resuming does not draft again."""
    note = state.get("note")
    reply = {"reply": model.invoke(f"{state['ticket']} {note or ''}").content}
    again = state.get("chap_task_id") if note else None
    return {**hil_review(bridge, reply, kind="draft_reply", input_={"ticket": state["ticket"]},
                         existing_task_id=again), "note": None}


def review(state: State) -> State:
    """Pause while the person decides, then read the decision from CHAP and
    apply it. This node runs again from the top when the graph resumes."""
    interrupt({"task_id": state["chap_task_id"], "draft": state["chap_artefact"]})
    decision = bridge.wait_for_decision(state["chap_task_id"])
    try:
        return {"reply": bridge.apply_decision(state["chap_task_id"], decision, state["chap_artefact"])}
    except ReviewRejected as rejected:
        if rejected.outcome == "revise":
            return {"note": rejected.rationale}
        return {"reply": None, "rejected": rejected.rationale}


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
builder.add_conditional_edges("review", lambda state: "draft" if state.get("note") else "send",
                              ["draft", "send"])
builder.add_edge("send", END)
graph = builder.compile(checkpointer=InMemorySaver())


def alice_decides(task_id: str, method: str, **params: Any) -> None:
    """Alice's desk: her decision, made in her own name."""
    out = coord.dispatch({"jsonrpc": "2.0", "id": f"alice-{task_id}", "method": method,
                          "params": {"workspace": WORKSPACE, "from": ALICE, "task_id": task_id, **params}})
    if "error" in out:
        raise SystemExit(f"{method} was refused: {out['error']['message']}")


def handle(thread: str, ticket: str, *decisions: tuple) -> None:
    config = {"configurable": {"thread_id": thread}}
    out = graph.invoke({"ticket": ticket}, config)
    for method, params in decisions:
        shown = out["__interrupt__"][0].value
        print(f"  {thread}: Alice sees {shown['draft']['reply']!r} and decides {method}")
        alice_decides(shown["task_id"], method, **params)
        out = graph.invoke(Command(resume=True), config)
    print(f"  {thread}: {out['reply']['reply'] if out.get('reply') else 'rejected: ' + out['rejected']}")


def main() -> None:
    handle("ticket-1", "Where is my refund?", ("decide.approve", {"comment": "fine"}))
    handle("ticket-2", "My order arrived broken.", ("decide.override", {
        "diff": [{"op": "replace", "path": "/reply",
                  "value": "I am sorry it arrived broken. Your refund is on its way."}],
        "rationale": "too curt for an upset customer", "tags": ["tone-warmed"], "intent_preserved": True}))
    handle("ticket-3", "Can I have a discount?",
           ("decide.reject", {"comment": "we do not refund twice", "request_revision": True}),
           ("decide.approve", {}))

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
                               "params": {"workspace": WORKSPACE}})
    print(f"\nChain verified: {verified['result']}")


if __name__ == "__main__":
    main()
