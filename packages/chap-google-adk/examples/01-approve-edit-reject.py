"""
Example: approve, edit, and reject one confirmation-gated Google ADK tool,
and read the CHAP audit chain that results.

Run (needs the optional ADK extra):
    pip install -e ".[google-adk]"
    python3 examples/01-approve-edit-reject.py

It drives ADK with a scripted model, so it runs offline with no API key.
The model proposes a `transfer` call and the run pauses for confirmation.
The script makes each confirmation for the human, the bridge records it on
a hash-linked chain, and the same ToolConfirmation resumes the run. An
edit travels in the confirmation's payload, and apply_reviewed_args runs
the tool with it, so every transfer below ran as the chain records it.
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

from chap_coordinator import Coordinator
from chap_google_adk import ChapConfirmationBridge, apply_reviewed_args

CONFIRM = "adk_request_confirmation"
ORIGINAL = {"amount": 100, "to": "Alice"}


def transfer(amount: int, to: str) -> str:
    print(f"    transfer ran: {amount} to {to}")
    return f"sent {amount} to {to}"


class ScriptedLlm(BaseLlm):
    """Proposes the transfer, then a closing line once the tool answered."""

    async def generate_content_async(self, llm_request, stream=False):
        answered = any(part.function_response for content in llm_request.contents
                       for part in (content.parts or []))
        if answered:
            yield LlmResponse(content=types.Content(
                role="model", parts=[types.Part(text="done")]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(
                    name="transfer", args=dict(ORIGINAL)))]))


def _new_runner() -> Runner:
    agent = LlmAgent(name="pay", model=ScriptedLlm(model="scripted"),
                     tools=[FunctionTool(transfer, require_confirmation=True)],
                     before_tool_callback=apply_reviewed_args)
    return Runner(app_name="pay", agent=agent,
                  session_service=InMemorySessionService())


async def _pause(runner, sid):
    """Run until the confirmation pause; return the request-confirmation call."""
    await runner.session_service.create_session(
        app_name="pay", user_id="u", session_id=sid)
    msg = types.Content(role="user", parts=[types.Part(text="pay Alice 100")])
    paused = None
    async for event in runner.run_async(user_id="u", session_id=sid, new_message=msg):
        for part in (event.content.parts if event.content else []):
            fc = part.function_call
            if fc and fc.name == CONFIRM:
                paused = fc
    if paused is None:
        raise RuntimeError("no confirmation request")
    return paused


async def _resume(runner, sid, conf_id, confirmation):
    msg = types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=conf_id, name=CONFIRM, response=confirmation.model_dump()))])
    async for _ in runner.run_async(user_id="u", session_id=sid, new_message=msg):
        pass


async def decide(bridge, sid, confirmation, **kw):
    print(f"  {sid}: {confirmation.model_dump(exclude={'hint'})}")
    runner = _new_runner()
    conf_fc = await _pause(runner, sid)
    tool_call = conf_fc.args["originalFunctionCall"]      # {name, args, id}
    bridge.record_decision(tool_call, confirmation, **kw)
    await _resume(runner, sid, conf_fc.id, confirmation)


async def main() -> None:
    coord = Coordinator(enable_chain=True)
    bridge = ChapConfirmationBridge(
        coord, workspace="wsp_payments",
        agent="agent:assistant#v1", reviewer="human:alice@example.org")

    # Approve as proposed.
    await decide(bridge, "s1", ToolConfirmation(confirmed=True))

    # Refining edit: cap the amount, same decision -> intent_preserved true.
    await decide(bridge, "s2",
                 ToolConfirmation(confirmed=True, payload={"args": {**ORIGINAL, "amount": 50}}),
                 approver="human:sam@example.org",
                 rationale="over the desk limit; capped to 50", tags=["limit-exceeded"])

    # Substituting edit: redirect the payment, a different decision.
    await decide(bridge, "s3",
                 ToolConfirmation(confirmed=True, payload={"args": {**ORIGINAL, "to": "Escrow"}}),
                 approver="human:sam@example.org",
                 rationale="wrong recipient; routed to escrow", tags=["wrong-recipient"],
                 intent_preserved=False)

    # Reject.
    await decide(bridge, "s4", ToolConfirmation(confirmed=False),
                 rationale="recipient not allow-listed")

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
    asyncio.run(main())
