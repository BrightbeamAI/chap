"""
The HTTP client in the package: chap_coordinator.client.

The client lived only as a copy in each generated Python project, and the
MCP command's --url mode had an HTTP client of its own. In a workspace that
required signatures, the copy read the workspace with an unsigned
workspace.describe; the coordinator refused it, the client took the refusal
for a workspace with no members, and the agent waited for ever for a
reviewer who had joined. Its key ids were a hash of the URI, so every key a
participant ever had carried the same id.

Here the client runs against the package's own desk server.
"""
from __future__ import annotations

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import anyio
import pytest

from chap_coordinator.client import (
    ChapEndpointError, ChapError, HttpCoordinator, Participant, Signer, wait_for_decision,
)
from chap_coordinator.server import load_config, make_coordinator, make_server
from chap_coordinator.transports import remote

WORKSPACE = "wsp_client"
HUMAN = "human:you@local"
AGENT = "agent:drafter"


#: The enrolment code every desk server in these tests takes, which a
#: person's first join carries.
ENROL = "TESTS-ENROL"


def start(tmp_path: Path, monkeypatch, **fields):
    for name in ("PORT", "CHAP_HOST", "CHAP_DB_PATH", "CHAP_REQUIRE_SIGNATURES", "CHAP_CHAIN", "CHAP_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHAP_ENROLMENT_CODE", ENROL)
    path = tmp_path / "chap.config.json"
    path.write_text(json.dumps({
        "workspace": WORKSPACE, "profiles": ["core/1.0", "review/1.0"], "store": ":memory:", "port": 0,
        "humans": [{"uri": HUMAN, "display_name": "You", "role": "reviewer"}], "agent": {"uri": AGENT}, **fields,
    }), encoding="utf-8")
    config = load_config(path)
    server = make_server(config, make_coordinator(config))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/chap"


@pytest.fixture
def signed(tmp_path, monkeypatch):
    """A desk server whose workspace requires signatures, with the reviewer
    joined from the desk with a key, as the browser does, and the agent
    joined with its key file."""
    server, url = start(tmp_path, monkeypatch, require_signatures=True)
    client = HttpCoordinator(url)
    human = Participant(client, WORKSPACE, HUMAN, Signer.load_or_create(HUMAN, tmp_path / "desk-keys"), enrolment=ENROL)
    agent = Participant(client, WORKSPACE, AGENT, Signer.load_or_create(AGENT, tmp_path / "keys"))
    human.join("human", "reviewer")
    agent.join("agent", "drafter")
    try:
        yield client, agent, human
    finally:
        server.shutdown()
        server.server_close()


def test_a_participant_that_signs_reads_a_workspace_that_requires_signatures(signed):
    client, agent, human = signed
    assert agent.reviewer_present() is True
    assert {m["uri"] for m in agent.members()} == {HUMAN, AGENT}
    assert agent.is_paused() is False
    # Unsigned, the read is refused, and the refusal is an error: it is not
    # an empty workspace.
    with pytest.raises(ChapError) as refused:
        Participant(client, WORKSPACE, AGENT).members()
    assert refused.value.code == -32070 and refused.value.method == "workspace.describe"


def test_a_signed_task_waits_for_the_persons_decision(signed):
    client, agent, human = signed
    task = agent.call("task.create", kind="draft", assignee=AGENT, input={"ticket": 1}, review_required=True)
    assert agent.call("task.complete", task_id=task["task_id"], output={"body": "draft"})["state"] == "review_requested"
    with pytest.raises(TimeoutError):
        agent.wait_for_decision(task["task_id"], poll_seconds=0.01, timeout=0.05)
    threading.Timer(0.2, lambda: human.call("decide.approve", task_id=task["task_id"])).start()
    view = wait_for_decision(client, task["task_id"], poll_seconds=0.02, timeout=5)
    assert view["state"] == "completed" and view["decision_log"][-1]["reviewer"] == HUMAN
    assert agent.task(task["task_id"])["output"] == {"body": "draft"}
    with pytest.raises(KeyError):
        agent.wait_for_decision("tsk_missing", poll_seconds=0.01)


def test_the_participant_stands_in_for_a_coordinator_in_a_signed_workspace(signed):
    client, agent, human = signed
    envelope = {"jsonrpc": "2.0", "id": "1", "method": "task.create", "params": {
        "workspace": WORKSPACE, "from": AGENT, "kind": "draft", "assignee": AGENT, "input": {}}}
    assert agent.dispatch(envelope)["result"]["state"] == "created"
    # An envelope from someone else goes as it came, and the coordinator refuses it.
    as_human = {**envelope, "params": {**envelope["params"], "from": HUMAN}}
    assert agent.dispatch(as_human)["error"]["code"] == -32070
    # What a bridge asks after a refused setup call, read as the participant.
    assert WORKSPACE in agent.workspaces and "wsp_other" not in agent.workspaces
    assert {HUMAN, AGENT} <= set(agent.workspaces.get(WORKSPACE).members)
    # From no one and unsigned, the same read is refused.
    assert WORKSPACE not in client.workspaces
    with pytest.raises(ValueError, match="holds the key of agent:drafter"):
        Participant(client, WORKSPACE, HUMAN, agent.signer)


def test_unsigned_the_client_reads_and_writes_as_before(tmp_path, monkeypatch):
    server, url = start(tmp_path, monkeypatch)
    try:
        client = HttpCoordinator(url)
        agent = Participant(client, WORKSPACE, AGENT)
        assert agent.reviewer_present() is True
        assert WORKSPACE in client.workspaces
        assert AGENT in client.workspaces.get(WORKSPACE).members
        assert client.dispatch({"jsonrpc": "2.0", "id": "2", "method": "no.such", "params": {}})["error"]["code"] == -32601
        assert client.get("/api/health")["ok"] is True and client.task("tsk_missing") is None
    finally:
        server.shutdown()
        server.server_close()


# RFC 8037, appendix A.1 and A.3: an Ed25519 key and its RFC 7638 thumbprint.
RFC8037_D = "nWGxne_9WmC6hEr0kuwsxERJxWl7MmkZcDusAxyuf2A"
RFC8037_X = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"
RFC8037_THUMBPRINT = "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"


def test_a_key_is_named_by_its_rfc_7638_thumbprint(tmp_path):
    path = Signer.key_path(AGENT, tmp_path)
    path.write_text(json.dumps({"kty": "OKP", "crv": "Ed25519", "d": RFC8037_D, "x": RFC8037_X}), encoding="utf-8")
    signer = Signer.load_or_create(AGENT, tmp_path)
    assert signer.kid == RFC8037_THUMBPRINT and signer.public_jwk["x"] == RFC8037_X

    # New keys: each its own id, in a file and a directory only the owner reads.
    first = Signer.load_or_create(AGENT, tmp_path / "keys")
    second = Signer.load_or_create(HUMAN, tmp_path / "keys")
    assert first.kid != second.kid
    assert Signer.load_or_create(AGENT, tmp_path / "keys").kid == first.kid
    if os.name == "posix":
        assert stat.S_IMODE(Signer.key_path(AGENT, tmp_path / "keys").stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / "keys").stat().st_mode) == 0o700

    # A key file from an earlier release keeps the id it was registered under.
    old = Signer.key_path("agent:old", tmp_path)
    old.write_text(json.dumps({"kty": "OKP", "crv": "Ed25519", "kid": "0123456789abcdef", "d": RFC8037_D,
                               "x": RFC8037_X}), encoding="utf-8")
    assert Signer.load_or_create("agent:old", tmp_path).kid == "0123456789abcdef"


class _Answering(BaseHTTPRequestHandler):
    """A server that answers every request with one status and body."""

    status = 421
    body = b'{"error": "unknown host; set allowed_hosts in chap.config.json"}'

    def log_message(self, *_args) -> None:
        pass

    def _answer(self) -> None:
        self.send_response(self.status)
        self.send_header("content-length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    do_GET = do_POST = _answer


def test_no_json_rpc_answer_raises_chap_endpoint_error():
    with pytest.raises(ChapEndpointError, match=r"at http://127\.0\.0\.1:9/chap could not be reached"):
        HttpCoordinator("http://127.0.0.1:9/chap", timeout=2).dispatch({"jsonrpc": "2.0", "id": 1, "method": "x"})
    for status, body, said in [
        (421, b'{"error": "unknown host"}', "refused the call with HTTP 421: unknown host"),
        (502, b"<html>Bad gateway</html>", "answered HTTP 502 with a body that is not JSON"),
    ]:
        handler = type("Handler", (_Answering,), {"status": status, "body": body})
        http = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        try:
            client = HttpCoordinator(f"http://127.0.0.1:{http.server_address[1]}/chap")
            with pytest.raises(ChapEndpointError, match=said):
                client.dispatch({"jsonrpc": "2.0", "id": 1, "method": "workspace.describe"})
            with pytest.raises(ChapEndpointError, match=f"answered HTTP {status}"):
                client.get("/api/health")
        finally:
            http.shutdown()
            http.server_close()


def test_the_mcp_commands_forwarding_uses_the_same_client(tmp_path, monkeypatch):
    assert remote.ChapEndpointError is ChapEndpointError
    server, url = start(tmp_path, monkeypatch)
    try:
        forward = remote.RemoteDispatcher(url)
        answer = anyio.run(forward.dispatch, {"jsonrpc": "2.0", "id": 1, "method": "workspace.describe",
                                              "params": {"workspace": WORKSPACE}})
        assert answer["result"]["id"] == WORKSPACE
    finally:
        server.shutdown()
        server.server_close()
