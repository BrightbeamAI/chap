"""
chap-google-adk with a real Google ADK runner: a scripted model (no API
key) proposes a confirmation-gated `transfer`, the run pauses, and the
ToolConfirmation that resumes it decides what the tool does. The tool
records what it ran with, so the tests compare that with the chain.

Skipped where google-adk is not installed; scripts/test-bridges.sh
installs it.
"""

import asyncio
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parents[1]))
sys.path.insert(0, str(THIS.parents[2] / "coordinator-py"))

import pytest

pytest.importorskip("google.adk")

from google.genai import types
from google.adk.agents import LlmAgent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import FunctionTool
from google.adk.tools.tool_confirmation import ToolConfirmation

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_google_adk import ChapConfirmationBridge, apply_reviewed_args


REVIEWER = "human:alice@example.org"
CONFIRM = "adk_request_confirmation"
PROPOSED = {"amount": 100, "to": "Alice"}


class ScriptedLlm(BaseLlm):
    """Proposes the transfer, then closes once the tool has answered."""

    async def generate_content_async(self, llm_request, stream=False):
        answered = any(part.function_response for content in llm_request.contents
                       for part in (content.parts or []))
        if answered:
            yield LlmResponse(content=types.Content(role="model",
                                                    parts=[types.Part(text="done")]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(
                    name="transfer", args=dict(PROPOSED)))]))


class Payments:
    """One ADK run of an agent whose transfer needs confirmation."""

    def __init__(self) -> None:
        self.ran: list = []

        def transfer(amount: int, to: str) -> str:
            self.ran.append({"amount": amount, "to": to})
            return f"sent {amount} to {to}"

        agent = LlmAgent(name="pay", model=ScriptedLlm(model="scripted"),
                         tools=[FunctionTool(transfer, require_confirmation=True)],
                         before_tool_callback=apply_reviewed_args)
        self.runner = Runner(app_name="pay", agent=agent,
                             session_service=InMemorySessionService())

    async def pause(self):
        """Run until the confirmation pause; return the confirmation call."""
        await self.runner.session_service.create_session(
            app_name="pay", user_id="u", session_id="s")
        message = types.Content(role="user", parts=[types.Part(text="pay Alice 100")])
        paused = None
        async for event in self.runner.run_async(user_id="u", session_id="s",
                                                 new_message=message):
            for part in (event.content.parts if event.content else []):
                call = part.function_call
                if call and call.name == CONFIRM:
                    paused = call
        assert paused is not None
        return paused

    async def resume(self, paused, confirmation: ToolConfirmation) -> None:
        message = types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(
                id=paused.id, name=CONFIRM, response=confirmation.model_dump()))])
        async for _ in self.runner.run_async(user_id="u", session_id="s",
                                             new_message=message):
            pass


def bridge_and_coord():
    coord = Coordinator(CoordinatorOptions(enable_chain=True))
    return coord, ChapConfirmationBridge(coord, workspace="wsp_adk",
                                         agent="agent:assistant#v1", reviewer=REVIEWER)


def overrides(bridge):
    return [e["envelope"]["params"]["diff"] for e in bridge.audit()
            if e["envelope"]["method"] == "decide.override"]


# ---- record a confirmation made elsewhere ----------------------------


def test_a_recorded_override_is_the_one_adk_runs():
    coord, bridge = bridge_and_coord()
    run = Payments()

    async def main():
        paused = await run.pause()
        tool_call = paused.args["originalFunctionCall"]
        confirmation = ToolConfirmation(
            confirmed=True, payload={"args": {**PROPOSED, "amount": 50}})
        bridge.record_decision(tool_call, confirmation, approver="human:sam@example.org",
                               rationale="over the desk limit")
        await run.resume(paused, confirmation)

    asyncio.run(main())
    assert run.ran == [{"amount": 50, "to": "Alice"}]
    assert overrides(bridge) == [[{"op": "replace", "path": "/args/amount", "value": 50}]]


def test_an_override_adk_would_not_run_is_not_recorded():
    coord, bridge = bridge_and_coord()
    run = Payments()

    async def main():
        paused = await run.pause()
        with pytest.raises(ValueError, match="only when ADK will run it"):
            bridge.record_decision(paused.args["originalFunctionCall"],
                                   ToolConfirmation(confirmed=True), decision="override",
                                   returned={**PROPOSED, "amount": 50})

    asyncio.run(main())
    assert overrides(bridge) == []


# ---- request and wait --------------------------------------------------


async def desk(coord, method, **params):
    """A person at a desk sharing the coordinator: they join, wait for the
    review addressed to them, and decide it."""
    coord.dispatch({"jsonrpc": "2.0", "id": "j", "method": "participant.join",
                    "params": {"workspace": "wsp_adk", "from": REVIEWER, "type": "human"}})
    for _ in range(500):
        for task in list(coord.workspaces["wsp_adk"].tasks.values()):
            if task.state == "review_requested":
                out = coord.dispatch({"jsonrpc": "2.0", "id": "d", "method": method,
                                      "params": {"workspace": "wsp_adk", "from": REVIEWER,
                                                 "task_id": task.id, **params}})
                assert "error" not in out, out
                return
        await asyncio.sleep(0.01)
    raise AssertionError("no review reached the desk")


def decide_at_the_desk(method, **params):
    coord, bridge = bridge_and_coord()
    run = Payments()

    async def main():
        paused = await run.pause()
        person = asyncio.ensure_future(desk(coord, method, **params))
        confirmation = await bridge.request_confirmation(
            paused.args["originalFunctionCall"], timeout=5, poll_seconds=0.01)
        await person
        await run.resume(paused, confirmation)
        return confirmation

    return asyncio.run(main()), run.ran, bridge


def test_an_approval_runs_the_call_as_proposed():
    confirmation, ran, bridge = decide_at_the_desk("decide.approve")
    assert (confirmation.confirmed, confirmation.payload) == (True, None)
    assert ran == [PROPOSED]
    decided = [e["envelope"]["params"]["from"] for e in bridge.audit()
               if e["envelope"]["method"].startswith("decide.")]
    assert decided == [REVIEWER]


def test_an_override_at_the_desk_runs_the_reviewers_arguments():
    confirmation, ran, _ = decide_at_the_desk(
        "decide.override", diff=[{"op": "replace", "path": "/args/to", "value": "Escrow"}],
        rationale="wrong recipient")
    assert confirmation.payload == {"args": {"amount": 100, "to": "Escrow"}}
    assert ran == [{"amount": 100, "to": "Escrow"}]


def test_a_rejection_at_the_desk_runs_nothing():
    confirmation, ran, _ = decide_at_the_desk("decide.reject", comment="not allow-listed")
    assert confirmation.confirmed is False
    assert ran == []
