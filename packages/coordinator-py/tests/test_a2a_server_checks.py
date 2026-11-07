"""
The A2A server's checks, and a round trip with the A2A SDK's own client.

make_chap_a2a_app puts ChapA2aGuard before the routes, so a page on another
site cannot call the coordinator: a foreign Host is refused with 421, a
foreign Origin with 403, a POST that is not JSON with 415, a body over the
limit with 413 before it is read, and an exception is a short JSON body.

Mirrors packages/coordinator-a2a/tests/guard.test.ts and round_trip.test.ts.
"""
from __future__ import annotations

import json

import httpx
import pytest
from a2a.client import ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from a2a.types import Message, Part, Role, SendMessageRequest, TaskState
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.a2a_app import ChapA2aGuard, make_chap_a2a_app
from chap_coordinator.transports import a2a_cli
from chap_coordinator.transports.a2a_cli import main
from chap_coordinator.transports.mcp_schemas import TOOL_NAMES
from chap_coordinator.transports.mcp_tools import AGENT_MCP_TOOLS

BASE = "http://127.0.0.1:9090"


def _coord() -> Coordinator:
    coord = Coordinator(CoordinatorOptions(deterministic_ids=True, default_profiles=["core/1.0", "review/1.0"]))
    for method, params in (("workspace.create", {"from": "human:alice"}),
                           ("participant.join", {"from": "human:alice", "type": "human"}),
                           ("participant.join", {"from": "agent:a", "type": "agent"}),
                           ("task.create", {"from": "agent:a", "kind": "payment", "assignee": "agent:a",
                                            "input": {"pay": "EUR 50000"}, "review_required": True})):
        reply = coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method, "params": {"workspace": "w", **params}})
    task_id = reply["result"]["task_id"]
    coord.dispatch({"jsonrpc": "2.0", "id": "c", "method": "task.complete",
                    "params": {"workspace": "w", "from": "agent:a", "task_id": task_id, "output": {"pay": "EUR 50000"}}})
    return coord


def _task_id(coord: Coordinator) -> str:
    return next(iter(coord.get_workspace("w").tasks))


def _v03(skill: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"kind": "message", "messageId": f"m-{skill}", "role": "user",
                                   "parts": [{"kind": "data", "data": {"skill": skill, "params": params}}]}}}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE)


def _state(coord: Coordinator) -> str:
    return coord.get_workspace("w").tasks[_task_id(coord)].state


@pytest.mark.asyncio
async def test_a_cross_site_simple_request_no_longer_approves_as_alice():
    coord = _coord()
    approve = json.dumps(_v03("chap.decide.approve", {"workspace": "w", "from": "human:alice",
                                                       "task_id": _task_id(coord), "comment": "approved"}))
    async with _client(make_chap_a2a_app(coord, base_url=f"{BASE}/")) as client:
        cross_site = await client.post("/", content=approve, headers={
            "origin": "https://evil.example", "content-type": "text/plain;charset=UTF-8",
            "sec-fetch-mode": "no-cors", "sec-fetch-site": "cross-site"})
        assert cross_site.status_code == 403
        assert cross_site.json() == {"error": "cross-origin requests are refused"}
        no_origin = await client.post("/", content=approve, headers={"content-type": "text/plain"})
        assert no_origin.status_code == 415
        assert no_origin.json() == {"error": "POST takes application/json"}
    assert _state(coord) == "review_requested"


@pytest.mark.asyncio
async def test_a_foreign_host_is_refused_before_any_route_and_an_allowed_one_is_served():
    coord = _coord()
    async with _client(make_chap_a2a_app(coord, base_url=f"{BASE}/", allowed_hosts=["a2a.internal:9999"])) as client:
        for path, method in (("/.well-known/agent-card.json", "GET"), ("/", "POST")):
            r = await client.request(method, path, headers={"host": "rebind.evil.example:9090",
                                                           "content-type": "application/json"},
                                     content=json.dumps(_v03("chap.workspace.create", {"workspace": "x"})))
            assert r.status_code == 421, path
            assert r.json() == {"error": "unknown host; add it to the allowed hosts to serve under that name"}
        assert coord.get_workspace("x") is None
        r = await client.get("/.well-known/agent-card.json", headers={"host": "a2a.internal:9999"})
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_a_foreign_or_null_origin_is_refused_and_the_servers_own_or_none_is_served():
    coord = _coord()
    body = json.dumps(_v03("chap.workspace.describe", {"workspace": "w"}))
    async with _client(make_chap_a2a_app(coord, base_url=f"{BASE}/")) as client:
        for origin in ("https://evil.example", "null"):
            r = await client.post("/", content=body, headers={"content-type": "application/json", "origin": origin})
            assert r.status_code == 403, origin
        twice = await client.post("/", content=body, headers=[
            ("content-type", "application/json"), ("origin", BASE), ("origin", "https://evil.example")])
        assert twice.status_code == 403, "two Origin headers name no one origin"
        for headers in ({"origin": BASE}, {}):
            r = await client.post("/", content=body, headers={"content-type": "application/json", **headers})
            assert r.status_code == 200, headers


@pytest.mark.asyncio
async def test_a_body_over_the_limit_is_refused_before_it_is_read():
    coord = _coord()
    app = make_chap_a2a_app(coord, base_url=f"{BASE}/", max_body_bytes=300_000)
    big = json.dumps(_v03("chap.workspace.create", {"workspace": "big", "pad": "x" * 400_000})).encode()
    async with _client(app) as client:
        declared = await client.post("/", content=big, headers={"content-type": "application/json"})
        assert declared.status_code == 413
        assert declared.json() == {"error": "the request body is over 300000 bytes"}
        assert declared.headers["connection"] == "close"

        async def chunks():
            yield big[:200_000]
            yield big[200_000:]

        streamed = await client.post("/", content=chunks(), headers={"content-type": "application/json"})
        assert streamed.status_code == 413
    assert coord.get_workspace("big") is None


def _nested(depth: int) -> bytes:
    """A SendMessage whose call's params carry ``x``, nested ``depth`` arrays deep: the body nests 7 + depth levels."""
    return ('{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"messageId":"m-%d",'
            '"role":"ROLE_USER","parts":[{"data":{"skill":"chap.workspace.describe","params":{"workspace":"w","x":'
            '%s%s}},"mediaType":"application/json"}]}}}' % (depth, "[" * depth, "]" * depth)).encode()


@pytest.mark.asyncio
async def test_a_body_nesting_deeper_than_32_levels_is_refused_with_400_as_json_and_one_at_32_is_served():
    headers = {"content-type": "application/json", "A2A-Version": "1.0"}
    async with _client(make_chap_a2a_app(_coord(), base_url=f"{BASE}/")) as client:
        at_limit = await client.post("/", headers=headers, content=_nested(25))
        assert at_limit.status_code == 200, at_limit.text
        assert "nests deeper" not in at_limit.text
        # 100,000 levels is past json.loads' own recursion limit.
        for depth in (26, 100_000):
            r = await client.post("/", headers=headers, content=_nested(depth))
            assert r.status_code == 400, depth
            assert r.headers["content-type"] == "application/json"
            assert r.json() == {"jsonrpc": "2.0", "id": None, "error": {
                "code": -32600, "message": "Invalid Request: the body nests deeper than 32 levels"}}


async def _send_cut_short(app, body: bytes, *, cut_short: bool) -> list:
    """POST ``body`` straight to the ASGI app. Cut short, the client declares
    500 bytes more than it sends, and goes away before they arrive."""
    declared = len(body) + (500 if cut_short else 0)
    incoming = [{"type": "http.request", "body": body, "more_body": cut_short}]
    if cut_short:
        incoming.append({"type": "http.disconnect"})

    async def receive():
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    sent: list = []

    async def send(message):
        sent.append(message)

    await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
               "path": "/", "raw_path": b"/", "root_path": "", "query_string": b"",
               "server": ("127.0.0.1", 9090), "client": ("127.0.0.1", 50000),
               "headers": [(b"host", b"127.0.0.1:9090"), (b"content-type", b"application/json"),
                           (b"a2a-version", b"1.0"), (b"content-length", str(declared).encode())]},
              receive, send)
    return sent


@pytest.mark.asyncio
async def test_a_call_whose_body_was_cut_short_is_never_run():
    coord = Coordinator()
    app = make_chap_a2a_app(coord, base_url=f"{BASE}/", skill_filter=lambda _name: True)

    def create(workspace: str) -> bytes:
        return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": {"message": {
            "messageId": workspace, "role": "ROLE_USER", "parts": [{"mediaType": "application/json", "data": {
                "skill": "chap.workspace.create", "params": {"workspace": workspace}}}]}}}).encode()

    assert await _send_cut_short(app, create("cut"), cut_short=True) == []
    assert coord.get_workspace("cut") is None
    # The same call sent in full runs.
    answered = await _send_cut_short(app, create("whole"), cut_short=False)
    assert answered[0]["status"] == 200
    assert coord.get_workspace("whole") is not None


@pytest.mark.asyncio
async def test_a_body_that_is_not_json_is_a_parse_error():
    async with _client(make_chap_a2a_app(_coord(), base_url=f"{BASE}/")) as client:
        r = await client.post("/", content=b"{not json", headers={"content-type": "application/json"})
        assert r.status_code == 400
        assert r.json() == {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}


@pytest.mark.asyncio
async def test_an_exception_that_escapes_the_app_reaches_the_client_as_a_short_json_body(caplog):
    async def failing(scope, receive, send):
        raise RuntimeError("cannot open /srv/chap/data/chap.db")

    async with _client(ChapA2aGuard(failing)) as client:
        r = await client.get("/")
    assert r.status_code == 500
    assert r.json() == {"error": "internal error"}
    assert "/srv/chap" in caplog.text


class _Internal:
    """Answers as the coordinator does when a handler raises."""

    def dispatch(self, envelope):
        return {"jsonrpc": "2.0", "id": envelope["id"],
                "error": {"code": -32603, "message": "Internal error",
                          "data": {"detail": "unhashable type: 'list' in /srv/chap/coordinator.py"}}}


@pytest.mark.asyncio
async def test_a_coordinators_internal_error_reaches_a_client_as_a_failed_task_without_its_detail():
    async with _client(make_chap_a2a_app(_Internal(), base_url=f"{BASE}/")) as client:
        r = await client.post("/", json=_v03("chap.workspace.describe", {"workspace": []}))
    task = r.json()["result"]
    assert task["status"]["state"] == "failed"
    assert task["status"]["message"]["parts"][0]["data"] == {
        "chap_error": -32603, "message": "The CHAP call failed inside the server; its log has the detail."}
    assert "unhashable" not in r.text


def _message(skill: str, params: dict) -> Message:
    data = Value()
    ParseDict({"skill": skill, "params": params}, data)
    return Message(message_id=f"m-{skill}-{len(params)}", role=Role.ROLE_USER,
                   parts=[Part(data=data, media_type="application/json")])


@pytest.mark.asyncio
async def test_an_a2a_sdk_client_round_trips_and_a_refusal_is_a_rejected_task():
    coord = _coord()
    app = make_chap_a2a_app(coord, base_url=f"{BASE}/", participant="agent:a")
    async with _client(app) as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
        assert card["url"] == f"{BASE}/", "a 0.3 client finds the endpoint"
        client = ClientFactory(ClientConfig(httpx_client=http, streaming=False)).create(parse_agent_card(card))

        async def send(skill: str, params: dict):
            events = [event async for event in client.send_message(SendMessageRequest(message=_message(skill, params)))]
            assert len(events) == 1
            return events[0]

        described = await send("chap.workspace.describe", {"workspace": "w"})
        assert described.HasField("message")
        assert MessageToDict(described.message.parts[0].data)["id"] == "w"

        refused = await send("chap.decide.approve", {"workspace": "w", "task_id": _task_id(coord)})
        assert refused.HasField("task")
        assert refused.task.status.state == TaskState.TASK_STATE_REJECTED
        assert MessageToDict(refused.task.status.message.parts[0].data)["chap_error"] == -32011

        as_alice = await send("chap.decide.approve", {"workspace": "w", "from": "human:alice", "task_id": _task_id(coord)})
        assert as_alice.task.status.state == TaskState.TASK_STATE_REJECTED
        assert "acts as agent:a" in MessageToDict(as_alice.task.status.message.parts[0].data)["message"]
    assert _state(coord) == "review_requested"


@pytest.mark.asyncio
async def test_a_v0_3_client_is_still_answered_and_sees_a_refusal_as_a_rejected_task():
    coord = _coord()
    async with _client(make_chap_a2a_app(coord, base_url=f"{BASE}/")) as client:
        ok = (await client.post("/", json=_v03("chap.workspace.describe", {"workspace": "w"}))).json()
        assert ok["result"]["kind"] == "message"
        assert ok["result"]["parts"][0]["data"]["id"] == "w"
        refused = (await client.post("/", json=_v03("chap.workspace.describe", {"workspace": "missing"}))).json()
        assert refused["result"]["kind"] == "task"
        assert refused["result"]["status"]["state"] == "rejected"
        assert refused["result"]["status"]["message"]["parts"][0]["data"]["chap_error"] == -32602


@pytest.mark.parametrize("argv, message", [
    (["--as", "human:me@local"], "names a person"),
    (["--as", "not a uri"], "is not a participant URI"),
    (["--port", "70000"], "--port takes a port number"),
])
def test_the_command_refuses_a_bad_option(argv, message, capsys):
    assert main(argv) == 2
    assert message in capsys.readouterr().err


def test_the_command_refuses_a_human_named_by_chap_participant(monkeypatch, capsys):
    monkeypatch.setenv("CHAP_PARTICIPANT", "human:ceo@corp.example")
    assert main([]) == 2
    assert "CHAP_PARTICIPANT human:ceo@corp.example names a person" in capsys.readouterr().err


def test_the_command_lists_its_options(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for option in ("--host", "--port", "--as URI", "--skills {agent,all}", "--db PATH", "--allowed-host NAME"):
        assert option in out


@pytest.mark.asyncio
@pytest.mark.parametrize("argv, skills", [([], AGENT_MCP_TOOLS), (["--skills", "all"], set(TOOL_NAMES))])
async def test_the_command_offers_the_agents_skills_unless_told_to_offer_every_one(argv, skills, monkeypatch):
    served = {}
    monkeypatch.setattr(a2a_cli.uvicorn, "run", lambda app, **_options: served.setdefault("app", app))
    assert main(argv) == 0
    async with _client(served["app"]) as client:
        card = (await client.get("/.well-known/agent-card.json", headers={"A2A-Version": "1.0"})).json()
    assert {skill["id"] for skill in card["skills"]} == skills
