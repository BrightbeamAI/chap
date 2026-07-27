"""
Regression: the reference server must turn malformed requests into clean
protocol errors instead of crashing or hanging the request thread. A
non-numeric or negative Content-Length and a deeply nested body used to raise
uncaught exceptions (a negative length even triggered an unbounded read).
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
def address():
    coord = Coordinator(CoordinatorOptions(
        default_profiles=["core/1.0", "review/1.0"],
    ))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(coord))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv.server_address
    finally:
        srv.shutdown()


def _post(address, content_length, body: bytes, timeout: float = 3.0) -> bytes:
    host, port = address
    s = socket.create_connection((host, port), timeout=timeout)
    s.settimeout(timeout)
    request = (
        f"POST /chap HTTP/1.0\r\n"
        f"Host: localhost\r\n"
        f"Content-Type: application/json\r\n"
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


def test_within_depth_helper():
    from server import _within_depth
    assert _within_depth({"a": [1, 2, {"b": 3}]}, 64)
    node = inner = {}
    for _ in range(100):
        inner["x"] = {}
        inner = inner["x"]
    assert not _within_depth(node, 64)
