"""
Regression: the reference server must turn malformed requests into clean
protocol errors instead of crashing or hanging the request thread. A
non-numeric or negative Content-Length and a deeply nested body used to raise
uncaught exceptions (a negative length even triggered an unbounded read).

It answers under its own names only, refuses a call from another origin, and
takes JSON only, so a page in a browser cannot act on it.
"""
from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

THIS = Path(__file__).resolve()
sys.path.insert(0, str(THIS.parent))
sys.path.insert(0, str(THIS.parents[2] / "packages" / "coordinator-py"))

import pytest

from chap_coordinator import Coordinator, CoordinatorOptions
from server import make_handler


@pytest.fixture
def served():
    coord = Coordinator(CoordinatorOptions(
        default_profiles=["core/1.0", "review/1.0"],
    ))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(coord))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address, coord
    finally:
        srv.shutdown()


@pytest.fixture
def address(served):
    return served[0]


JSON = "Content-Type: application/json\r\n"


def _post(address, content_length, body: bytes, timeout: float = 3.0,
          headers: str = JSON, host_name: str | None = None) -> bytes:
    host, port = address
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    request = (
        f"POST /chap HTTP/1.0\r\n"
        f"Host: {host_name or f'localhost:{port}'}\r\n"
        f"{headers}"
        f"Content-Length: {content_length}\r\n"
        f"\r\n"
    ).encode("utf-8") + body
    s.sendall(request)
    buf = b""
    try:
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
    except socket.timeout:
        pass
    finally:
        s.close()
    return buf


def _body(raw: bytes):
    if b"\r\n\r\n" not in raw:
        return None
    payload = raw.split(b"\r\n\r\n", 1)[1]
    if not payload:
        return None
    return json.loads(payload.decode("utf-8"))


def test_non_numeric_content_length(address):
    resp = _body(_post(address, "abc", b"{}"))
    assert resp is not None and resp["error"]["code"] == -32700


def test_negative_content_length(address):
    resp = _body(_post(address, "-1", b""))
    assert resp is not None and resp["error"]["code"] == -32700


def test_deeply_nested_body_rejected(address):
    depth = 200
    body = ('{"a":' * depth + "1" + "}" * depth).encode("utf-8")
    resp = _body(_post(address, str(len(body)), body))
    assert resp is not None
    assert resp["error"]["message"] == "Envelope nesting too deep"


def test_valid_request_still_works(address):
    env = {"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
           "params": {"workspace": "w", "profiles": ["core/1.0"]}}
    body = json.dumps(env).encode("utf-8")
    resp = _body(_post(address, str(len(body)), body))
    assert resp is not None and "result" in resp


def _create(address, workspace: str, headers: str = JSON, host_name: str | None = None) -> tuple[int, dict]:
    """POST a workspace.create; the answer's status and body."""
    body = json.dumps({"jsonrpc": "2.0", "id": "1", "method": "workspace.create",
                       "params": {"workspace": workspace, "profiles": ["core/1.0"]}}).encode("utf-8")
    raw = _post(address, str(len(body)), body, headers=headers, host_name=host_name)
    return int(raw.split(b" ", 2)[1]), _body(raw)


def test_a_call_that_is_not_json_is_refused_and_takes_no_effect(served):
    address, coord = served
    for headers in ("Content-Type: text/plain\r\n", "Content-Type: application/x-www-form-urlencoded\r\n", ""):
        status, body = _create(address, "w-plain", headers=headers)
        assert (status, body) == (415, {"error": "POST /chap takes application/json"})
    assert coord.get_workspace("w-plain") is None
    assert _create(address, "w-json", headers="Content-Type: application/json; charset=utf-8\r\n")[0] == 200


def test_a_call_from_another_origin_is_refused_and_takes_no_effect(served):
    address, coord = served
    port = address[1]
    for origin in ("Origin: https://elsewhere.example\r\n", "Origin: null\r\n",
                   f"Origin: http://localhost:{port}\r\nOrigin: https://elsewhere.example\r\n"):
        status, body = _create(address, "w-foreign", headers=JSON + origin)
        assert (status, body) == (403, {"error": "cross-origin requests are refused"})
    assert coord.get_workspace("w-foreign") is None
    assert _create(address, "w-own", headers=JSON + f"Origin: http://localhost:{port}\r\n")[0] == 200


def test_a_call_under_another_host_name_is_refused_unless_it_is_allowed(served, monkeypatch):
    address, coord = served
    for name in ("elsewhere.example", "localhost", f"elsewhere.example:{address[1]}", ""):
        status, body = _create(address, "w-rebound", host_name=name or " ")
        assert status == 421 and body["error"].startswith("unknown host")
    assert coord.get_workspace("w-rebound") is None
    monkeypatch.setenv("CHAP_ALLOWED_HOSTS", "chap.internal, Desk.Example:8443")
    assert _create(address, "w-proxied", host_name="desk.example:8443")[0] == 200
    assert _create(address, "w-proxied-origin", host_name="chap.internal",
                   headers=JSON + "Origin: https://desk.example:8443\r\n")[0] == 200


def test_within_depth_helper():
    from server import _within_depth
    assert _within_depth({"a": [1, 2, {"b": 3}]}, 64)
    node = inner = {}
    for _ in range(100):
        inner["x"] = {}
        inner = inner["x"]
    assert not _within_depth(node, 64)
