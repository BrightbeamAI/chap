"""
The store a CHAP agent's refusals are kept in: the most recent ones only,
each without the request it refused, so memory stays bounded however many
calls are refused.

Mirrors packages/coordinator-a2a/tests/task_store.test.ts.
"""
from __future__ import annotations

import json

import httpx
import pytest
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.types import ListTasksRequest, Message, Part, Role, Task, TaskState, TaskStatus

from chap_coordinator.coordinator import Coordinator
from chap_coordinator.transports.a2a_app import make_chap_a2a_app
from chap_coordinator.transports.a2a_server import ChapTaskStore

CONTEXT = ServerCallContext()


def _refusal(task_id: str) -> Task:
    agent = Message(message_id=f"{task_id}-agent", role=Role.ROLE_AGENT, parts=[Part(text="refused")])
    return Task(id=task_id, context_id="c",
                status=TaskStatus(state=TaskState.TASK_STATE_REJECTED, message=agent),
                history=[Message(message_id=f"{task_id}-user", role=Role.ROLE_USER, parts=[Part(text="x" * 1000)]),
                         agent])


@pytest.mark.asyncio
async def test_it_keeps_the_most_recent_tasks_the_oldest_going_first():
    store = ChapTaskStore(max_tasks=2)
    for task_id in ("t1", "t2", "t3"):
        await store.save(_refusal(task_id), CONTEXT)
    assert await store.get("t1", CONTEXT) is None
    assert await store.get("t2", CONTEXT) is not None
    assert await store.get("t3", CONTEXT) is not None

    # Saved again, a task becomes the newest.
    await store.save(_refusal("t2"), CONTEXT)
    await store.save(_refusal("t4"), CONTEXT)
    assert await store.get("t3", CONTEXT) is None
    listed = await store.list(ListTasksRequest(), CONTEXT)
    assert sorted(t.id for t in listed.tasks) == ["t2", "t4"]


@pytest.mark.asyncio
async def test_a_task_is_kept_without_the_clients_messages_and_the_agents_status_message_stays():
    store = ChapTaskStore()
    await store.save(_refusal("t1"), CONTEXT)
    kept = await store.get("t1", CONTEXT)
    assert [m.role for m in kept.history] == [Role.ROLE_AGENT]
    assert kept.status.state == TaskState.TASK_STATE_REJECTED
    assert kept.status.message.message_id == "t1-agent"


class _Named(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "other"


@pytest.mark.asyncio
async def test_a_caller_sees_only_its_own_tasks():
    store = ChapTaskStore()
    await store.save(_refusal("t1"), CONTEXT)
    assert await store.get("t1", ServerCallContext(user=_Named())) is None
    assert await store.get("t1", CONTEXT) is not None


@pytest.mark.parametrize("max_tasks", [0, -1, 1.5, True])
def test_max_tasks_must_be_a_whole_number_1_or_more(max_tasks):
    with pytest.raises(ValueError):
        ChapTaskStore(max_tasks=max_tasks)


@pytest.mark.asyncio
async def test_the_app_keeps_a_refused_call_without_the_body_it_carried():
    app = make_chap_a2a_app(Coordinator(), base_url="http://127.0.0.1:9090/")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:9090") as http:
        headers = {"content-type": "application/json", "A2A-Version": "1.0"}
        sent = await http.post("/", headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": {
                "messageId": "m1", "role": "ROLE_USER", "parts": [{"mediaType": "application/json", "data": {
                    "skill": "chap.workspace.describe", "params": {"workspace": "missing", "pad": "x" * 100_000}}}]}}})
        task = sent.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_REJECTED"
        got = await http.post("/", headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "GetTask", "params": {"id": task["id"]}})
        kept = got.json()["result"]
        assert kept["status"]["state"] == "TASK_STATE_REJECTED"
        assert "ROLE_USER" not in [m.get("role") for m in kept.get("history", [])]
        assert len(json.dumps(kept)) < 2_000, "the request's 100 kB are not kept"
