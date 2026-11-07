"""
The Python MCP server as a command, run as an MCP client runs it.

``python -m chap_coordinator.transports.mcp_cli`` acts as one participant,
the mcp-gate template's agent by default, offers the agent's tools, refuses
to act as a person, expands ``~`` in CHAP_DB_PATH, and with ``--url``
forwards every call to a running CHAP server, whose people decide in its
desk.

Mirrors packages/coordinator-mcp/tests/cli.test.ts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from chap_coordinator.coordinator import Coordinator, CoordinatorOptions
from chap_coordinator.transports.mcp_cli import main
from chap_coordinator.transports.mcp_schemas import TOOL_NAMES
from chap_coordinator.transports.mcp_server import AGENT_MCP_TOOLS

MODULE = "chap_coordinator.transports.mcp_cli"


@asynccontextmanager
async def _serve(*args: str, env: dict[str, str] | None = None):
    """Start the command as an MCP server; yield a client and a reader for its stderr."""
    with tempfile.TemporaryFile(mode="w+") as errlog:
        params = StdioServerParameters(command=sys.executable, args=["-m", MODULE, *args], env=env or {})
        async with Client(stdio_client(params, errlog=errlog)) as client:
            def stderr() -> str:
                errlog.seek(0)
                return errlog.read()
            yield client, stderr


def _text(result) -> str:
    return result.content[0].text if result.content else ""


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """Run the command to completion with stdin closed: for options it refuses or answers at once."""
    return subprocess.run([sys.executable, "-m", MODULE, *args], input="", capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, timeout=30)


# -- who the server acts as, and what it offers ---------------------------------

@pytest.mark.asyncio
async def test_by_default_it_acts_as_the_assistant_and_offers_the_agent_tools():
    async with _serve() as (client, stderr):
        tools = (await client.list_tools()).tools
        assert sorted(t.name for t in tools) == sorted(AGENT_MCP_TOOLS)
        create = next(t for t in tools if t.name == "chap.task.create")
        assert "from" not in create.input_schema.get("required", [])
        assert "acts as agent:assistant" in create.input_schema["properties"]["from"]["description"]
        hidden = await client.call_tool("chap.participant.join",
                                        {"workspace": "w", "from": "human:me@local", "type": "human"})
        assert hidden.is_error
        assert "not offered by this server: chap.participant.join" in _text(hidden)
        assert "acting as agent:assistant with the agent tools" in stderr()


@pytest.mark.asyncio
async def test_with_every_tool_a_call_naming_a_person_is_refused_and_the_log_credits_the_agent():
    async with _serve("--tools", "all") as (client, _):
        assert len((await client.list_tools()).tools) == len(TOOL_NAMES)
        assert not (await client.call_tool("chap.workspace.create", {"workspace": "w"})).is_error
        as_human = await client.call_tool("chap.participant.join",
                                          {"workspace": "w", "from": "human:me@local", "type": "human"})
        assert as_human.is_error
        assert 'acts as agent:assistant; a call cannot name another from ("human:me@local")' in _text(as_human)
        assert not (await client.call_tool("chap.participant.join", {"workspace": "w", "type": "agent"})).is_error
        log = json.loads(_text(await client.call_tool("chap.audit.read", {"workspace": "w"})))
        assert [e["envelope"]["params"]["from"] for e in log["entries"]] == ["agent:assistant"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("args, env, uri", [
    (["--as", "agent:bot@example.org"], {}, "agent:bot@example.org"),
    ([], {"CHAP_PARTICIPANT": "agent:from-env@example.org"}, "agent:from-env@example.org"),
    (["--as=agent:flag@example.org"], {"CHAP_PARTICIPANT": "agent:from-env@example.org"}, "agent:flag@example.org"),
])
async def test_as_and_chap_participant_name_the_participant(args, env, uri):
    async with _serve(*args, "--tools", "all", env=env) as (client, _):
        assert not (await client.call_tool("chap.workspace.create", {"workspace": "w"})).is_error
        log = json.loads(_text(await client.call_tool("chap.audit.read", {"workspace": "w"})))
        assert log["entries"][0]["envelope"]["params"]["from"] == uri


@pytest.mark.parametrize("args, env", [
    (["--as", "human:me@local"], {}),
    ([], {"CHAP_PARTICIPANT": "human:ceo@corp.example"}),
])
def test_a_human_participant_is_refused_before_the_server_starts(args, env, monkeypatch, capsys):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert main(args) == 2
    assert "names a person" in capsys.readouterr().err


# -- options ----------------------------------------------------------------------

def test_help_prints_the_options_and_the_agent_tools(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for option in ("--as URI", "--tools {agent,all}", "--url ENDPOINT", "--db PATH", "--profiles LIST"):
        assert option in out
    for name in AGENT_MCP_TOOLS:
        assert name in out


@pytest.mark.parametrize("args, message", [
    (["--bogus"], "unrecognized arguments: --bogus"),
    (["--tools", "some"], "invalid choice: 'some'"),
    (["--as", "not a uri"], "is not a participant URI"),
    (["--url", "file:///etc/passwd"], "--url takes the http or https address"),
    (["--url", "http://[::1"], "--url takes the http or https address"),
    (["--ur", "http://127.0.0.1:44999/chap"], "unrecognized arguments: --ur"),
    (["--url", "http://127.0.0.1:44999/chap", "--db", "x.db"], "--db does not apply"),
    (["--profiles", ","], "names no profiles"),
])
def test_an_unknown_option_or_a_bad_value_is_a_usage_error(args, message, capsys):
    assert main(args) == 2
    assert message in capsys.readouterr().err


def test_without_db_or_url_stderr_says_the_state_is_in_memory_only():
    r = _run()
    assert r.returncode == 0, r.stderr
    assert "State is in memory only" in r.stderr
    assert "the agent tools cannot create one" in r.stderr


def test_a_leading_tilde_in_chap_db_path_is_the_home_directory(tmp_path):
    r = _run(env={"HOME": str(tmp_path), "CHAP_DB_PATH": "~/chap/chap.db"})
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "chap" / "chap.db").exists()
    assert not Path("~").exists()
    assert f"Persisting to {tmp_path / 'chap' / 'chap.db'}" in r.stderr


# -- --url: forwarding to a running server --------------------------------------------

class _Desk:
    """A desk server in miniature: a coordinator behind POST /chap, with a
    reviewer and the assistant joined. The person's decisions are made here,
    in process, as the desk would make them."""

    def __init__(self) -> None:
        self.coord = Coordinator(CoordinatorOptions(deterministic_ids=True,
                                                    default_profiles=["core/1.0", "review/1.0"]))
        for method, params in (("workspace.create", {"from": "human:you@local"}),
                               ("participant.join", {"from": "human:you@local", "type": "human", "role": "reviewer"}),
                               ("participant.join", {"from": "agent:assistant", "type": "agent"})):
            self.coord.dispatch({"jsonrpc": "2.0", "id": method, "method": method,
                                 "params": {"workspace": "w", **params}})
        coord = self.coord
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:
                pass

            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                with lock:
                    reply = json.dumps(coord.dispatch(json.loads(body))).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}/chap"
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.http.shutdown()
        self.http.server_close()


@pytest.mark.asyncio
async def test_url_forwards_each_call_as_the_participant_and_the_person_decides_at_the_server():
    desk = _Desk()
    try:
        async with _serve("--url", desk.url, "--as", "agent:assistant") as (client, stderr):
            made = await client.call_tool("chap.task.create", {
                "workspace": "w", "kind": "draft", "assignee": "agent:assistant",
                "input": {"ticket": 1}, "review_required": True})
            assert not made.is_error, _text(made)
            task_id = json.loads(_text(made))["task_id"]
            done = await client.call_tool("chap.task.complete",
                                          {"workspace": "w", "task_id": task_id, "output": {"body": "draft"}})
            assert json.loads(_text(done))["state"] == "review_requested"

            own = await client.call_tool("chap.decide.approve", {"workspace": "w", "task_id": task_id})
            assert own.is_error
            assert json.loads(_text(own))["chap_error"] == -32011
            as_human = await client.call_tool("chap.decide.approve",
                                              {"workspace": "w", "task_id": task_id, "from": "human:you@local"})
            assert as_human.is_error
            assert "acts as agent:assistant;" in _text(as_human)
            assert desk.coord.get_workspace("w").tasks[task_id].state == "review_requested"

            desk.coord.dispatch({"jsonrpc": "2.0", "id": "desk", "method": "decide.approve",
                                 "params": {"workspace": "w", "from": "human:you@local", "task_id": task_id}})
            log = json.loads(_text(await client.call_tool(
                "chap.audit.read", {"workspace": "w", "filter": {"task_id": task_id}})))
            decided = [e["envelope"]["params"]["from"] for e in log["entries"]
                       if (e.get("envelope") or {}).get("method") == "decide.approve"]
            assert decided == ["human:you@local"]
            assert f"Forwarding every call to {desk.url}" in stderr()
    finally:
        desk.close()


@pytest.mark.asyncio
async def test_url_with_no_as_acts_as_the_agent_a_desk_made_from_the_mcp_gate_template_joins():
    template = json.loads((Path(__file__).resolve().parents[2] / "create-chap-app" / "templates" / "mcp-gate"
                           / "template.json").read_text())
    assert template["defaults"]["agent_uri"] == "agent:assistant"
    desk = _Desk()
    try:
        async with _serve("--url", desk.url) as (client, _):
            made = await client.call_tool("chap.task.create", {
                "workspace": "w", "kind": "draft", "assignee": "agent:assistant", "input": {}})
            assert not made.is_error, _text(made)
            task = desk.coord.get_workspace("w").tasks[json.loads(_text(made))["task_id"]]
            assert task.delegator == "agent:assistant"
    finally:
        desk.close()


@pytest.mark.asyncio
async def test_url_reports_a_server_it_cannot_reach_or_one_that_refuses_the_request():
    async with _serve("--url", "http://127.0.0.1:9/chap") as (client, _):
        r = await client.call_tool("chap.workspace.describe", {"workspace": "w"})
        assert r.is_error
        assert "The CHAP server at http://127.0.0.1:9/chap could not be reached" in _text(r)

    class Refusing(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_POST(self) -> None:
            reply = b'{"error": "unknown host; set allowed_hosts in chap.config.json"}'
            self.send_response(421)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

    http = ThreadingHTTPServer(("127.0.0.1", 0), Refusing)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{http.server_address[1]}/chap"
        async with _serve("--url", url) as (client, _):
            r = await client.call_tool("chap.workspace.describe", {"workspace": "w"})
            assert r.is_error
            assert "refused the call with HTTP 421: unknown host; set allowed_hosts" in _text(r)
    finally:
        http.shutdown()
        http.server_close()
