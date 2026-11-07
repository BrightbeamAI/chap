"""
Example: Google ADK tool calls that need confirmation, decided at a CHAP
desk, and the CHAP audit chain that results.

Run (needs the optional ADK extra):
    pip install -e ".[google-adk]"
    python3 examples/01-approve-edit-reject.py

It drives ADK with a scripted model, so it runs offline with no API key.
The model proposes a call and the run pauses for confirmation. The bridge
opens a review of the paused call and waits; Sam decides at a desk that
shares the coordinator, where a task on the event loop stands in for the
desk: an approval, an edit that caps the amount, an edit that sends the
money elsewhere, a rejection, and the number of days a leave tool asks for.
The run resumes with the confirmation the bridge returns, and every call
below ran as the chain records it. Last, a confirmation made in this
script's own interface is recorded in Sam's name, marked as recorded.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "coordinator-py"))

from google.genai import types
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.adk.tools.tool_confirmation import ToolConfirmation
from google.adk.tools.tool_context import ToolContext

from chap_coordinator import Coordinator
from chap_google_adk import ChapConfirmationBridge, apply_reviewed_args

WORKSPACE = "wsp_payments"
SAM = "human:sam@example.org"
CONFIRM = "adk_request_confirmation"

coord = Coordinator(enable_chain=True)
bridge = ChapConfirmationBridge(coord, workspace=WORKSPACE, agent="agent:assistant#v1", reviewer=SAM)


def transfer(amount: int, to: str) -> str:
    print(f"    transfer ran: {amount} to {to}")
    return f"sent {amount} to {to}"


def take_leave(days: int, tool_context: ToolContext) -> str:
    """A tool that asks for a payload of its own, as in ADK's docs."""
    if not tool_context.tool_confirmation:
        tool_context.request_confirmation(hint="How many days do you approve?", payload={"approved_days": 0})
        return "waiting for a manager"
    granted = min(tool_context.tool_confirmation.payload["approved_days"], days)
    print(f"    take_leave ran: {granted} of {days} days")
    return f"granted {granted} of {days} days"


class ScriptedLlm(BaseLlm):
    """Proposes one call, then closes once the tool has answered."""

    call: dict = {}

    async def generate_content_async(self, llm_request, stream=False):
        if any(part.function_response for content in llm_request.contents for part in (content.parts or [])):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(**self.call))]))


async def sams_desk(method: str, **params) -> None:
    """Sam's desk: it decides the next review that opens, in Sam's name."""
    def call(name, **more):
        return coord.dispatch({"jsonrpc": "2.0", "id": f"desk-{name}", "method": name,
                               "params": {"workspace": WORKSPACE, "from": SAM, **more}})

    done = len(call("audit.read", filter={"method": "task.complete"})["result"]["entries"])
    while len(submitted := call("audit.read", filter={"method": "task.complete"})["result"]["entries"]) <= done:
        await asyncio.sleep(0.02)
    review = submitted[-1]["envelope"]["params"]
    print(f"  Sam sees {review['output']['tool']}{review['output']['args']} and decides {method}")
    call(method, task_id=review["task_id"], **params)


async def run(call: dict, decide) -> None:
    """One run: the model proposes `call`, the run pauses, `decide(paused, agent)` confirms it."""
    model = ScriptedLlm(model="scripted")
    model.call = call
    agent = LlmAgent(name="pay", model=model,
                     tools=[FunctionTool(transfer, require_confirmation=True), take_leave],
                     before_tool_callback=apply_reviewed_args)
    runner = Runner(app_name="pay", agent=agent, session_service=InMemorySessionService())
    await runner.session_service.create_session(app_name="pay", user_id="u", session_id="s")
    paused = None
    async for event in runner.run_async(user_id="u", session_id="s", new_message=types.Content(
            role="user", parts=[types.Part(text="go")])):
        for part in (event.content.parts if event.content else []):
            if part.function_call and part.function_call.name == CONFIRM:
                paused = part.function_call
    confirmation = await decide(paused, agent)
    async for _ in runner.run_async(user_id="u", session_id="s", new_message=types.Content(
            role="user", parts=[types.Part(function_response=types.FunctionResponse(
                id=paused.id, name=CONFIRM, response=confirmation.model_dump()))])):
        pass


def at_the_desk(method: str, **params):
    async def decide(paused, agent):
        desk = asyncio.create_task(sams_desk(method, **params))
        confirmation = await bridge.request_confirmation(paused, llm_agent=agent, poll_seconds=0.1)
        await desk
        return confirmation
    return decide


async def main() -> None:
    pay = {"name": "transfer", "args": {"amount": 100, "to": "Alice"}}
    await run(pay, at_the_desk("decide.approve", comment="fine"))
    await run(pay, at_the_desk("decide.override", rationale="over the desk limit; capped to 50",
                               tags=["limit-exceeded"], diff=[{"op": "replace", "path": "/args/amount", "value": 50}]))
    await run(pay, at_the_desk("decide.override", rationale="wrong recipient; routed to escrow",
                               intent_preserved=False, diff=[{"op": "replace", "path": "/args/to", "value": "acct-escrow"}]))
    await run(pay, at_the_desk("decide.reject", comment="recipient not on the allow-list"))
    await run({"name": "take_leave", "args": {"days": 5}},
              at_the_desk("decide.override", rationale="two days this month",
                          diff=[{"op": "replace", "path": "/payload/approved_days", "value": 2}]))

    async def on_the_phone(paused, agent):
        confirmation = ToolConfirmation(confirmed=True, payload={"args": {"amount": 20, "to": "Alice"}})
        bridge.record_decision(paused, confirmation, llm_agent=agent, rationale="agreed on the phone")
        print("  recorded: agreed on the phone, 20 to Alice")
        return confirmation

    await run(pay, on_the_phone)

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
    asyncio.run(main())
