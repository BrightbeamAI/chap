"""
chap-google-adk with a real Google ADK runner: a scripted model (no API
key) proposes a call that needs confirmation, the run pauses, and the
ToolConfirmation that resumes it decides what the tool does. The tools
record what they ran with, so the tests compare that with the chain.

Skipped where google-adk is not installed; scripts/test-bridges.sh
installs it, and fails where they are skipped.
"""

import asyncio
import json
import sys
import threading
import time
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
from google.adk.tools.tool_context import ToolContext

from chap_coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.client import HttpCoordinator, Participant, Signer
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_google_adk import ChapConfirmationBridge, apply_reviewed_args

WS = "wsp_adk"
REVIEWER = "human:alice@example.org"
CONFIRM = "adk_request_confirmation"
PROPOSED = {"amount": 100, "to": "Alice"}


class ScriptedLlm(BaseLlm):
    """Proposes one call, then closes once a tool has answered."""

    call: dict = {}

    async def generate_content_async(self, llm_request, stream=False):
        if any(part.function_response for content in llm_request.contents for part in (content.parts or [])):
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="done")]))
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[
                types.Part(function_call=types.FunctionCall(**self.call))]))


class Run:
    """One ADK run of an agent whose tools need confirmation."""

    def __init__(self, call=None, edits=True) -> None:
        self.ran: list = []
        self.answered: list = []

        def transfer(amount: int, to: str) -> str:
            self.ran.append({"amount": amount, "to": to})
            return f"sent {amount} to {to}"

        def take_leave(days: int, tool_context: ToolContext) -> str:
            """A tool that asks for a payload of its own, as in ADK's docs."""
            if not tool_context.tool_confirmation:
                tool_context.request_confirmation(hint="How many days?", payload={"approved_days": 0})
                return "waiting"
            granted = tool_context.tool_confirmation.payload["approved_days"]
            self.ran.append({"requested": days, "granted": granted})
            return f"granted {granted} of {days}"

        model = ScriptedLlm(model="scripted")
        model.call = call or {"name": "transfer", "args": dict(PROPOSED)}
        self.agent = LlmAgent(name="pay", model=model,
                              tools=[FunctionTool(transfer, require_confirmation=True), take_leave],
                              **({"before_tool_callback": apply_reviewed_args} if edits else {}))
        self.runner = Runner(app_name="pay", agent=self.agent, session_service=InMemorySessionService())

    async def pause(self):
        """Run until the confirmation pause; return the adk_request_confirmation call."""
        await self.runner.session_service.create_session(app_name="pay", user_id="u", session_id="s")
        paused = None
        async for event in self.runner.run_async(user_id="u", session_id="s", new_message=types.Content(
                role="user", parts=[types.Part(text="go")])):
            for part in (event.content.parts if event.content else []):
                if part.function_call and part.function_call.name == CONFIRM:
                    paused = part.function_call
        assert paused is not None
        return paused

    async def resume(self, paused, confirmation: ToolConfirmation) -> None:
        async for event in self.runner.run_async(user_id="u", session_id="s", new_message=types.Content(
                role="user", parts=[types.Part(function_response=types.FunctionResponse(
                    id=paused.id, name=CONFIRM, response=confirmation.model_dump()))])):
            for part in (event.content.parts if event.content else []):
                if part.function_response:
                    self.answered.append(part.function_response.response)


def make(coord=None):
    coord = coord or Coordinator(CoordinatorOptions(enable_chain=True))
    return coord, ChapConfirmationBridge(coord, workspace=WS, agent="agent:assistant#v1", reviewer=REVIEWER)


async def desk(send, method, delay=0.0, **params):
    """A person at a desk: they decide the first review that opens, in their own name."""
    def call(name, **more):
        return send({"jsonrpc": "2.0", "id": f"desk-{name}-{time.monotonic_ns()}", "method": name,
                     "params": {"workspace": WS, "from": REVIEWER, **more}})

    for _ in range(500):
        submitted = call("audit.read", filter={"method": "task.complete"})["result"]["entries"]
        if submitted:
            await asyncio.sleep(delay)
            return call(method, task_id=submitted[0]["envelope"]["params"]["task_id"], **params)
        await asyncio.sleep(0.01)
    raise AssertionError("no review reached the desk")


def at_the_desk(method, run=None, edits_run=True, timeout=5, delay=0.0, **params):
    """The run pauses, the bridge asks the desk, and the run resumes with what the desk decided."""
    coord, bridge = make()
    run = run or Run()

    async def main():
        paused = await run.pause()
        person = asyncio.ensure_future(desk(coord.dispatch, method, delay, **params))
        confirmation = await bridge.request_confirmation(
            paused, llm_agent=run.agent if edits_run else None, timeout=timeout, poll_seconds=0.01)
        answer = await person
        await run.resume(paused, confirmation)
        return confirmation, answer

    confirmation, answer = asyncio.run(main())
    return run, coord, confirmation, answer


def task(coord):
    (only,) = coord.workspaces[WS].tasks.values()
    return only


# ---- the person decides at a desk ----------------------------------------


def test_an_approval_runs_the_call_as_proposed():
    run, coord, confirmation, _ = at_the_desk("decide.approve")
    assert confirmation.confirmed and run.ran == [PROPOSED]
    assert task(coord).review_required is True


def test_an_edit_of_the_arguments_runs_them():
    run, _, confirmation, _ = at_the_desk("decide.override", rationale="cap",
                                          diff=[{"op": "replace", "path": "/args/amount", "value": 50}])
    assert confirmation.payload == {"args": {"amount": 50, "to": "Alice"}}
    assert run.ran == [{"amount": 50, "to": "Alice"}]


def test_an_edit_the_agent_would_not_run_runs_nothing():
    """Without apply_reviewed_args ADK would run the model's arguments, so
    the edit is not confirmed, and nothing runs."""
    for run, edits_run in ((Run(edits=False), True), (Run(), False)):
        run, _, confirmation, _ = at_the_desk("decide.override", run=run, edits_run=edits_run, rationale="cap",
                                              diff=[{"op": "replace", "path": "/args/amount", "value": 50}])
        assert not confirmation.confirmed and "apply_reviewed_args" in confirmation.hint
        assert run.ran == []


@pytest.mark.parametrize("diff", [
    [{"op": "replace", "path": "/args", "value": None}],                          # the arguments made null
    [{"op": "remove", "path": "/args/to"}],                                        # one the tool needs taken away
    [{"op": "add", "path": "/args/memo", "value": "refund for #42"}],              # one the tool does not take
    [{"op": "replace", "path": "/tool", "value": "refund"}],                       # another tool
])
def test_an_edit_adk_would_not_run_as_decided_runs_nothing(diff):
    run, _, confirmation, _ = at_the_desk("decide.override", diff=diff, rationale="edit")
    assert not confirmation.confirmed and run.ran == []


def test_a_tools_own_payload_is_reviewed_and_the_tool_reads_the_persons_version():
    run, coord, confirmation, _ = at_the_desk(
        "decide.override", run=Run(call={"name": "take_leave", "args": {"days": 5}}), rationale="two will do",
        diff=[{"op": "replace", "path": "/payload/approved_days", "value": 2}])
    assert confirmation.payload == {"approved_days": 2}
    assert run.ran == [{"requested": 5, "granted": 2}]
    assert task(coord).pending_artefact["hint"] == "How many days?"


def test_a_rejection_runs_nothing():
    run, _, confirmation, _ = at_the_desk("decide.reject", comment="not on the allow-list")
    assert (confirmation.confirmed, confirmation.hint, run.ran) == (False, "not on the allow-list", [])


def test_a_revision_runs_nothing_and_closes_its_task():
    run, coord, confirmation, _ = at_the_desk("decide.reject", comment="split it", request_revision=True)
    assert not confirmation.confirmed and run.ran == [] and task(coord).state == "declined"


def test_a_call_nobody_decides_in_time_runs_nothing_and_its_review_is_withdrawn():
    run, coord, confirmation, answer = at_the_desk("decide.approve", timeout=0.2, delay=0.5)
    assert not confirmation.confirmed and "Nobody decided" in confirmation.hint
    assert answer["error"]["code"] == -32010 and run.ran == []
    assert task(coord).state == "in_progress"


def test_a_cancelled_wait_withdraws_the_review():
    coord, bridge = make()
    run = Run()

    async def main():
        paused = await run.pause()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bridge.request_confirmation(paused, llm_agent=run.agent, poll_seconds=0.05), 0.2)

    asyncio.run(main())
    assert task(coord).state == "in_progress"


# ---- a confirmation made in your own code ---------------------------------


def recorded(confirmation, run=None, edits_run=True, **kwargs):
    coord, bridge = make()
    run = run or Run()

    async def main():
        paused = await run.pause()
        error = None
        try:
            bridge.record_decision(paused, confirmation, llm_agent=run.agent if edits_run else None, **kwargs)
        except ValueError as exc:
            error = exc
        await run.resume(paused, confirmation)
        return error

    error = asyncio.run(main())
    decided = [(e["envelope"]["method"], e["envelope"]["params"].get("diff")) for e in bridge.audit()
               if "envelope" in e and e["envelope"]["method"].startswith("decide.")]
    return run, decided, error


def test_a_recorded_edit_is_the_one_adk_runs():
    run, decided, error = recorded(ToolConfirmation(confirmed=True, payload={"args": {**PROPOSED, "amount": 50}}),
                                   rationale="over the desk limit")
    assert error is None
    assert decided == [("decide.override", [{"op": "replace", "path": "/args/amount", "value": 50}])]
    assert run.ran == [{**PROPOSED, "amount": 50}]


def test_an_edit_adk_would_not_run_is_not_recorded():
    for run, edits_run, args in ((Run(edits=False), True, {**PROPOSED, "amount": 50}),
                                 (Run(), False, {**PROPOSED, "amount": 50}),
                                 (Run(), True, {"amount": 50})):
        _, decided, error = recorded(ToolConfirmation(confirmed=True, payload={"args": args}),
                                     run=run, edits_run=edits_run)
        assert isinstance(error, ValueError) and decided == []


def test_a_tools_own_payload_is_recorded_as_the_tool_reads_it():
    run, decided, error = recorded(ToolConfirmation(confirmed=True, payload={"approved_days": 2}),
                                   run=Run(call={"name": "take_leave", "args": {"days": 5}}))
    assert error is None
    assert decided == [("decide.override", [{"op": "replace", "path": "/payload/approved_days", "value": 2}])]
    assert run.ran == [{"requested": 5, "granted": 2}]


# ---- over HTTP, at a desk where people sign -------------------------------


def test_request_and_wait_against_a_desk_where_people_sign(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "chap.config.json"
    path.write_text(json.dumps({
        "workspace": WS, "profiles": ["core/1.0", "review/1.0"], "store": ":memory:", "port": 0,
        "require_signatures": "humans", "chain": True,
        "humans": [{"uri": REVIEWER, "display_name": "Alice", "role": "reviewer"}],
        "agent": {"uri": "agent:assistant#v1"},
    }), encoding="utf-8")
    config = load_config(path)
    server = make_server(config, make_coordinator(config))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        http = HttpCoordinator(f"http://127.0.0.1:{server.server_port}/chap")
        alice = Participant(http, WS, REVIEWER, Signer.load_or_create(REVIEWER, tmp_path / "keys"))
        alice.join("human", "reviewer")
        bridge = ChapConfirmationBridge(Participant(http, WS, "agent:assistant#v1"), workspace=WS,
                                        agent="agent:assistant#v1", reviewer=REVIEWER)
        run = Run()

        async def main():
            paused = await run.pause()
            person = asyncio.ensure_future(desk(
                lambda envelope: http.dispatch(alice.signer.sign(envelope)), "decide.override",
                rationale="cap", diff=[{"op": "replace", "path": "/args/amount", "value": 7}]))
            confirmation = await bridge.request_confirmation(paused, llm_agent=run.agent, timeout=5,
                                                             poll_seconds=0.02)
            await person
            await run.resume(paused, confirmation)

        asyncio.run(main())
        assert run.ran == [{"amount": 7, "to": "Alice"}]
    finally:
        server.shutdown()
        server.server_close()
