"""
Example: approve, edit, and reject one confirmation-gated Google ADK tool,
and read the CHAP audit chain that results.

Run (needs the optional ADK extra):
    pip install -e ".[google-adk]"
    python3 examples/01-approve-edit-reject.py

It drives ADK with a scripted model, so it runs offline with no API key.
The model proposes a `transfer` call; the run pauses for confirmation, the
bridge records the human's decision, and a `ToolConfirmation` resumes the
run -- as decide.approve / decide.override / decide.reject.
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
from chap_google_adk import ChapConfirmationBridge

CONFIRM = "adk_request_confirmation"
ORIGINAL = {"amount": 100, "to": "Alice"}


def transfer(amount: int, to: str) -> str:
    return f"sent {amount} to {to}"


class ScriptedLlm(BaseLlm):
    """Proposes the transfer on the first turn, then a closing line."""

    calls: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        if self.calls == 1:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(
                    name="transfer", args=dict(ORIGINAL)))]))
        else:
            yield LlmResponse(content=types.Content(
                role="model", parts=[types.Part(text="done")]))


def _new_runner() -> Runner:
    agent = LlmAgent(name="pay", model=ScriptedLlm(model="scripted"),
                     tools=[FunctionTool(transfer, require_confirmation=True)])
    return Runner(app_name="pay", agent=agent,
                  session_service=InMemorySessionService())


async def _pause(runner, sid):
    """Run until the confirmation pause; return the request-confirmation call."""
    await runner.session_service.create_session(
        app_name="pay", user_id="u", session_id=sid)
    msg = types.Content(role="user", parts=[types.Part(text="pay Alice 100")])
    async for event in runner.run_async(user_id="u", session_id=sid, new_message=msg):
        lr = getattr(event, "long_running_tool_ids", None) or set()
        for part in (event.content.parts if event.content else []):
            fc = part.function_call
            if fc and fc.name == CONFIRM and fc.id in lr:
                return fc
    raise RuntimeError("no confirmation request")


async def _resume(runner, sid, conf_id, confirmed):
    resp = {"confirmed": confirmed}
    msg = types.Content(role="user", parts=[types.Part(
        function_response=types.FunctionResponse(
            id=conf_id, name=CONFIRM, response=resp))])
    async for _ in runner.run_async(user_id="u", session_id=sid, new_message=msg):
        pass


async def decide(bridge, sid, *, confirmed, decision=None, **kw):
    runner = _new_runner()
    conf_fc = await _pause(runner, sid)
    tool_call = conf_fc.args["originalFunctionCall"]      # {name, args, id}
    bridge.record_decision(
        tool_call, ToolConfirmation(confirmed=confirmed),
        decision=decision, **kw)
    await _resume(runner, sid, conf_fc.id, confirmed)


async def main() -> None:
    coord = Coordinator()
    bridge = ChapConfirmationBridge(
        coord, workspace="wsp_payments",
        agent="agent:assistant#v1", reviewer="human:alice@example.org")

    # Approve as proposed (decision derived from confirmed=True).
    await decide(bridge, "s1", confirmed=True)

    # Refining edit: cap the amount, same decision -> intent_preserved true.
    await decide(bridge, "s2", confirmed=True, decision="override",
                 returned={**ORIGINAL, "amount": 50},
                 approver="human:sam@example.org",
                 rationale="over the desk limit; capped to 50", tags=["limit-exceeded"])

    # Substituting edit: redirect the payment, a different decision.
    await decide(bridge, "s3", confirmed=True, decision="override",
                 returned={**ORIGINAL, "to": "Escrow"},
                 approver="human:sam@example.org",
                 rationale="wrong recipient; routed to escrow", tags=["wrong-recipient"],
                 intent_preserved=False)

    # Reject (decision derived from confirmed=False).
    await decide(bridge, "s4", confirmed=False, rationale="recipient not allow-listed")

    print("Audit chain")
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
    asyncio.run(main())
