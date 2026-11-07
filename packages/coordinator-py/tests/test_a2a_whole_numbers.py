"""
Whole numbers cross the Python A2A server as integers, both ways.

a2a-sdk holds a data part's numbers as protobuf doubles. The server gives
whole numbers back their integer form on the way in, so a call's input keeps
1042, and on the way out, so a client reads error codes and sequence numbers
as integers, as the TypeScript server sends them.
"""
from __future__ import annotations

import re

import httpx
import pytest

from chap_coordinator.coordinator import Coordinator
from chap_coordinator.transports.a2a_app import make_chap_a2a_app

BASE = "http://127.0.0.1:9090"
HEADERS = {"content-type": "application/json", "A2A-Version": "1.0"}


def _send(skill: str, params: dict, message_id: str) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "method": "SendMessage", "params": {"message": {
        "messageId": message_id, "role": "ROLE_USER",
        "parts": [{"mediaType": "application/json", "data": {"skill": skill, "params": params}}]}}}


@pytest.mark.asyncio
async def test_whole_numbers_reach_the_coordinator_and_the_client_as_integers():
    coord = Coordinator()
    app = make_chap_a2a_app(coord, base_url=f"{BASE}/", skill_filter=lambda _name: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
        async def call(skill: str, params: dict, message_id: str) -> httpx.Response:
            return await client.post("/", headers=HEADERS, json=_send(skill, params, message_id))

        await call("chap.workspace.create", {"workspace": "w", "from": "agent:a"}, "m1")
        await call("chap.participant.join", {"workspace": "w", "from": "agent:a", "type": "agent"}, "m2")
        made = (await call("chap.task.create", {"workspace": "w", "from": "agent:a", "kind": "k", "assignee": "agent:a",
                                                "input": {"order": 1042}}, "m3")).json()
        task_id = made["result"]["message"]["parts"][0]["data"]["task_id"]
        assert coord.get_workspace("w").tasks[task_id].input == {"order": 1042}
        assert isinstance(coord.get_workspace("w").tasks[task_id].input["order"], int)

        log = await call("chap.audit.read", {"workspace": "w", "from": "agent:a"}, "m4")
        entries = log.json()["result"]["message"]["parts"][0]["data"]["entries"]
        assert [entry["seq"] for entry in entries] == list(range(len(entries)))
        assert all(isinstance(entry["seq"], int) for entry in entries)
        # A data part's members come out in no fixed order: seq may be the last.
        assert re.search(r'"seq":0[,}]', log.text), log.text

        refused = await call("chap.workspace.describe", {"workspace": "missing"}, "m5")
        assert '"chap_error":-32602' in refused.text
        assert refused.headers["content-length"] == str(len(refused.content))
