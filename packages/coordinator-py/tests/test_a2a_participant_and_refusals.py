"""
The executor pins ``from`` to one participant, and a refusal is a failure in
A2A's terms.

The executor had no participant option, so an A2A connection meant for one
agent could act in any member's name, a person's included. A refusal came
back as a successful message carrying ``chap_error``, so a client reading
A2A states saw every refused call succeed; and an exception was answered
with ``repr(exc)``. A refusal is now a task the agent rejected, and a fault
in the server a task that failed, without the exception's text.

Mirrors packages/coordinator-a2a/tests/participant_and_refusals.test.ts.
"""
from __future__ import annotations

import logging

import pytest
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.types import Message, Part, Role, SendMessageRequest, Task, TaskState
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct, Value

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.a2a_server import dispatch_a2a_message, make_chap_agent_executor


class _Queue(EventQueue):
    def __init__(self) -> None:
        self.events: list = []

    async def enqueue_event(self, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append(event)


def _message(skill, params: dict, *, metadata: dict | None = None) -> Message:
    data = Value()
    ParseDict({"skill": skill, "params": params}, data)
    msg = Message(message_id="m1", role=Role.ROLE_USER, parts=[Part(data=data)])
    if metadata is not None:
        md = Struct()
        ParseDict(metadata, md)
        msg.metadata.CopyFrom(md)
    return msg


def _coord() -> Coordinator:
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, default_profiles=["core/1.0", "review/1.0"]))
    for method, params in (("workspace.create", {"from": "human:alice"}),
                           ("participant.join", {"from": "human:alice", "type": "human"}),
                           ("participant.join", {"from": "agent:orchestrator", "type": "agent"})):
        coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": {"workspace": "w", **params}})
    return coord


async def _run(coord, message: Message, **options):
    queue = _Queue()
    ctx = RequestContext(call_context=ServerCallContext(), request=SendMessageRequest(message=message),
                         task_id="task-1", context_id="ctx-1")
    await make_chap_agent_executor(coord, **options).execute(ctx, queue)
    assert len(queue.events) == 1
    return queue.events[0]


def _refusal(event) -> tuple[Task, dict]:
    assert isinstance(event, Task), "a refusal is a task"
    part = event.status.message.parts[0]
    assert part.media_type == "application/json"
    return event, MessageToDict(part.data)


@pytest.mark.asyncio
async def test_a_refusal_is_a_rejected_task_and_its_status_message_carries_the_chap_error():
    task, error = _refusal(await _run(_coord(), _message("chap.workspace.describe", {"workspace": "missing"})))
    assert task.status.state == TaskState.TASK_STATE_REJECTED
    assert task.id == "task-1"
    assert task.context_id == "ctx-1"
    assert task.status.message.role == Role.ROLE_AGENT
    assert task.status.message.task_id == "task-1"
    assert error["chap_error"] == -32602
    assert isinstance(error["message"], str)
    assert task.history[0].message_id == "m1", "the history holds the request"


@pytest.mark.asyncio
async def test_an_answer_is_still_an_agent_message_with_no_task():
    event = await _run(_coord(), _message("chap.workspace.describe", {"workspace": "w"}))
    assert isinstance(event, Message)
    assert event.task_id == ""


class _Faulty:
    def dispatch(self, envelope):
        raise PermissionError("[Errno 13] Permission denied: '/srv/chap/data/chap.db'")


@pytest.mark.asyncio
async def test_a_fault_in_the_server_is_a_failed_task_and_its_text_stays_in_the_log(caplog):
    with caplog.at_level(logging.ERROR, logger="chap_coordinator.transports.a2a_server"):
        task, error = _refusal(await _run(_Faulty(), _message("chap.workspace.describe", {"workspace": "w"})))
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert error == {"chap_error": -32603,
                     "message": "The CHAP call failed inside the server; its log has the detail."}
    assert "/srv/chap" not in str(task)
    assert any(rec.exc_info and "/srv/chap" in str(rec.exc_info[1]) for rec in caplog.records)


class _Internal:
    """Answers as the coordinator does when a handler raises."""

    def dispatch(self, envelope):
        return {"jsonrpc": "2.0", "id": envelope["id"],
                "error": {"code": -32603, "message": "Internal error",
                          "data": {"detail": "unhashable type: 'list' in /srv/chap/coordinator.py"}}}


@pytest.mark.asyncio
async def test_a_coordinators_internal_error_is_a_failed_task_without_its_detail(caplog):
    with caplog.at_level(logging.ERROR, logger="chap_coordinator.transports.a2a_server"):
        task, error = _refusal(await _run(_Internal(), _message("chap.workspace.describe", {"workspace": []})))
    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert error == {"chap_error": -32603,
                     "message": "The CHAP call failed inside the server; its log has the detail."}
    assert "unhashable" not in str(task)
    assert "unhashable type" in caplog.text


@pytest.mark.asyncio
async def test_a_skill_id_that_is_not_a_string_is_an_unknown_skill():
    task, error = _refusal(await _run(_coord(), _message("chap.workspace.describe", {"workspace": "w"},
                                                         metadata={"skill": 5})))
    assert task.status.state == TaskState.TASK_STATE_REJECTED
    assert error["chap_error"] == -32601
    assert "AttributeError" not in error["message"]


@pytest.mark.asyncio
async def test_participant_fills_in_from_on_every_call():
    coord = _coord()
    event = await _run(coord, _message("chap.task.create",
                                       {"workspace": "w", "kind": "k", "input": {}, "assignee": "agent:orchestrator"}),
                       participant="agent:orchestrator")
    assert isinstance(event, Message)
    task_id = MessageToDict(event.parts[0].data)["task_id"]
    assert coord.get_workspace("w").tasks[task_id].delegator == "agent:orchestrator"


@pytest.mark.asyncio
async def test_with_participant_a_call_naming_a_person_is_refused_and_nothing_runs():
    coord = _coord()
    before = len(coord.get_workspace("w").audit)
    task, error = _refusal(await _run(
        coord, _message("chap.participant.join", {"workspace": "w", "from": "human:ceo@corp.example", "type": "human"}),
        participant="agent:orchestrator"))
    assert task.status.state == TaskState.TASK_STATE_REJECTED
    assert error["chap_error"] == -32011
    assert error["message"] == ('This server acts as agent:orchestrator; a call cannot name another '
                                'from ("human:ceo@corp.example").')
    assert len(coord.get_workspace("w").audit) == before
    assert "human:ceo@corp.example" not in coord.get_workspace("w").members


def test_without_participant_each_call_names_its_own_from_as_before():
    coord = _coord()
    reply = dispatch_a2a_message(coord, _message("chap.task.create", {
        "workspace": "w", "from": "human:alice", "kind": "k", "input": {}, "assignee": "agent:orchestrator"}))
    assert coord.get_workspace("w").tasks[reply["result"]["task_id"]].delegator == "human:alice"
